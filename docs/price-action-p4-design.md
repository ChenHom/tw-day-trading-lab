# Price Action P4 Design — Bar Persistence / Replay

Date: 2026-08-16

一句話定義：**程式重開後，仍能從保存資料重建出和原本完全相同的 canonical 1m / 5m。**

```text
MarketBar
    ↓
append-only event store
    ↓
latest revision view
```

實作在 `bars.py`：`append_bars` / `read_bar_events` / `load_latest_bars` / `load_bar_revision` / `bar_store_path`。

## 為什麼不先做資料庫

`sql/` 已有 TiDB schema，但 bar 是 append-only event，JSONL append 就完全夠用：一天一檔約 270 根 1m，掃描成本可忽略。加 `market_bars` table 需要 migration、repository、adapter 測試，換到的只是查詢語法。等到真的需要跨日跨檔查詢再說。

## Layout

```text
data/bars/{timeframe}/{date}/{symbol}.jsonl
```

`timeframe` 是**目錄層級**，所以同一個 symbol、同一個 `start_at` 的 1m 與 5m 在結構上就不可能 key collision——不必依賴 key 設計正確。

每行一個 `MarketBar` JSON，欄位與 `MarketBar.to_dict()` 相同。

## Append-only

correction 是**新增一行**，不是覆寫。

```text
09:00 1m rev1   ← 策略當時看到的
09:00 1m rev2   ← 盤後修正
```

兩行都在檔案裡。`load_latest_bars` 回 rev2，但 rev1 永遠可從 `read_bar_events` 取回。這是「不可靜默改寫已被策略使用的 bar」在持久層的對應：記憶體裡靠 frozen dataclass，磁碟上靠 append-only。

重跑同一場 session 會把事件再寫一次，事件數翻倍但 latest view 不變——`latest_bars()` 以 `(symbol, timeframe, start_at)` 收斂並取最大 revision。

## API

| 函式 | 用途 |
|---|---|
| `append_bars(store_dir, bars)` | 依 `(timeframe, date, symbol)` 分組 append；date 由 `start_at[:10]` 推得 |
| `read_bar_events(store_dir, *, timeframe, trading_date, symbol)` | 依寫入順序回傳所有 revision |
| `load_latest_bars(store_dir, *, timeframe, trading_date, symbols=None, start_at=None, end_at=None)` | 最新 revision view；`symbols` 省略時自動掃描該日目錄；`start_at` 含、`end_at` 不含 |
| `load_bar_revision(store_dir, *, timeframe, trading_date, symbol, start_at)` | 單一根 bar 的最新 revision |

CLI：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli bars build \
  --date 2026-08-17 --symbols 2330 \
  --cache-dir data/raw --store-dir data/bars
```

從 PA-P1 raw tick 重建 1m 與 5m，同時寫入 event store。寫入的是**完整 emission log**（含 correction），不是收斂後的 latest view。

## P4 Definition of Done

```text
[x] 1m 可以保存
[x] 5m 可以保存
[x] 同一 bar 不同 revision 不互相覆蓋
[x] 能查某根 bar 的 latest revision
[x] 能查一段時間的 latest bars
[x] replay 同一份 bar event log，結果完全一致
[x] restart 後可以恢復資料
[x] 1m / 5m 不會 key collision

→ PA-P4_CODE_COMPLETE（13 tests）
```

核心測試 `test_stored_events_rebuild_identical_canonical_bars`：跑一場 session → 全部寫入 → 只從磁碟讀回 → `to_dict()` 與記憶體結果完全相同。

PA-P4 沒有 Gate B。它不碰任何外部 API，正確性完全由 fixture 決定；真實資料的驗證屬於 PA-P1/P2/P3 的 Gate B。

## 不屬於 P4

TiDB `market_bars` table、跨日查詢、壓縮 / 輪替、事件去重（重跑產生重複事件是可接受的，latest view 不受影響）。

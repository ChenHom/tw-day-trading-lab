# Old Log / CSV Importer

日期：2026-05-28<br>
狀態：P1 MVP complete

## 目的

Old log importer 是反例測試器。它不是要復活舊策略，而是把舊專案的 `trade_decisions_YYYY-MM-DD.csv` 轉成新系統可驗證的樣本：

- `valid`
- `excluded`
- `needs_review`

策略 expectancy 只能使用 `validity = valid` 的樣本。`excluded` 與 `needs_review` 必須保留，不能刪掉，因為它們是防止新系統再次被污染的證據。

## 舊資料位置

目前已確認舊 repo 的主要輸入：

```text
/home/hom/services/stock/quantitative-trading-decision-system/logs/trade_decisions_YYYY-MM-DD.csv
```

實際欄位包含：

```text
id, position_id, timestamp, symbol, action, price, vwap, ma_5, reason,
entry_price, stop_loss, position_size, mfe, mae, realized_r, context
```

部分日期會多出 market / sector / shadow-trade 欄位。Importer 對額外欄位採忽略策略，避免舊格式小變動造成匯入中斷。

## CLI

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli old-logs import \
  --date 2026-03-25 \
  --input /home/hom/services/stock/quantitative-trading-decision-system/logs/trade_decisions_2026-03-25.csv \
  --output reports/old-log-2026-03-25-samples.json \
  --report-output reports/old-log-2026-03-25-failure.md
```

把 sample summary 接入 daily report：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli report daily \
  --date 2026-05-28 \
  --input reports/2026-05-28-candidates.json \
  --samples reports/old-log-2026-03-25-samples.json \
  --format md \
  --output reports/2026-05-28-daily-with-samples.md
```

## 分類規則

分類優先序：

1. malformed timestamp -> `needs_review / malformed_timestamp`
2. debug / forced / test marker -> `excluded / debug_or_forced_order`
3. 09:00 前或 13:30 後 entry -> `excluded / after_hours`
4. 同 symbol 同 timestamp 多筆 `ENTER` -> `excluded / duplicate_enter_same_symbol_timestamp`
5. 缺 `position_id` -> `needs_review / missing_position_id`
6. 缺 exit -> `needs_review / missing_exit`
7. exit timestamp malformed -> `needs_review / malformed_exit_timestamp`
8. 其他完整 lifecycle -> `valid`

## 函式與功能說明

| 名稱 | 類型 | 說明 |
|---|---|---|
| `parse_float` | pure function | 將 CSV 數字欄位轉成 `float`，空值或壞值回傳 `None`。 |
| `parse_timestamp` | pure function | 解析舊 CSV 的 ISO timestamp，壞格式回傳 `None`，不讓 importer crash。 |
| `parse_context` | pure function | 解析 `context` JSON；壞 JSON 回傳空 dict。 |
| `is_exit_action` | pure function | 判斷 action 是否為 `EXIT_*`。 |
| `is_debug_or_forced_event` | pure function | 從 reason / context 偵測 debug、forced、test、測試、強制、盤後等樣本。 |
| `is_after_hours` | pure function | 判斷 entry 是否在 09:00-13:30 之外。 |
| `TradeLogEvent.from_row` | factory | 將一列 CSV normalize 成 typed event。 |
| `load_csv_events` | IO adapter | 讀取 CSV 檔，轉成 `TradeLogEvent` list；這是 importer 的檔案邊界。 |
| `make_duplicate_enter_index` | pure function | 建立同 symbol 同 timestamp 的 ENTER 重複索引。 |
| `classify_lifecycle` | pure function | 對單一 entry lifecycle 做 `valid / excluded / needs_review` 分類。 |
| `normalize_lifecycles` | pure function | 將 ENTER/EXIT events group 成 `TradeLifecycle` list。 |
| `build_lifecycle` | pure function | 建立 `valid_samples` compatible lifecycle object。 |
| `make_setup_id` | pure function | 依 entry timestamp 產生 setup id，讓同分鐘同 setup 可被冪等鍵約束。 |
| `compute_realized_r` | pure function | 計算 gross R；若 partial exits 有 position size，採權重計算。 |
| `summarize_samples` | pure function | 統計 valid / excluded / needs_review 與排除原因。 |
| `import_trade_log_csv` | composition function | 串接 CSV 讀取、normalize、duplicate detection 與分類。 |
| `render_failure_replay_markdown` | pure function | 產生 failure replay Markdown report。 |
| `_duplicate_key` | private helper | 產生 duplicate-enter key，供重複進場偵測與分類共用。 |

## CLI / Report 整合功能說明

| 名稱 | 類型 | 說明 |
|---|---|---|
| `load_sample_summary` | IO adapter | 從 old-log import JSON 讀取 summary，提供 daily report 顯示樣本分級。 |
| `cmd_old_logs_import` | composition function | CLI 組裝點：讀 CSV、呼叫 importer、輸出 samples JSON 與 failure report。 |
| `cmd_report_daily --samples` | composition feature | 讓 daily report 可掛上 importer 的 valid / excluded / needs_review 統計。 |
| `render_markdown(..., sample_summary=...)` | pure rendering | Markdown 日報渲染；無 sample summary 時保留尚未接 replay ledger 的提示。 |
| `render_html(..., sample_summary=...)` | pure rendering | HTML 日報渲染；只接收已整理好的資料，不讀檔、不查 DB。 |
| `_format_sample_summary_markdown` | private helper | 將 summary 格式化為 Markdown bullet。 |
| `_format_sample_summary_text` | private helper | 將 summary 格式化為 HTML 文字。 |

## Pure DI / SOLID 設計

- 核心分類邏輯都吃 `TradeLogEvent` list，不直接讀檔。
- 檔案讀取只在 `load_csv_events` 這個 adapter。
- CLI 只負責組裝：讀 input、呼叫 importer、寫 output；核心邏輯沒有依賴 argparse。
- report rendering 不依賴檔案系統或 DB。
- TiDB adapter 尚未接入，因此 importer 不會依賴 DB client。
- P1 先用 pure functions + dataclass，不引入 DI container；等 P2 TiDB adapter 出現實際生命週期與連線需求時，再只在 composition root 組裝。

## 驗證結果

### sample fixture

```text
valid / excluded / needs_review: 1 / 3 / 1
```

### 舊 log 2026-03-25

```text
events: 1410
valid / excluded / needs_review: 0 / 8 / 0
duplicate_enter_same_symbol_timestamp: 8
```

### 舊 log 2026-04-01

```text
events: 2467
valid / excluded / needs_review: 0 / 8 / 0
duplicate_enter_same_symbol_timestamp: 2
after_hours: 6
```

## TDD 紀錄

先寫 `tests/test_old_log_importer.py`，第一次執行失敗：

```text
ModuleNotFoundError: No module named 'tw_day_trading_lab.old_log_importer'
```

之後才實作 `old_log_importer.py` 與 CLI。完成後：

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
10 tests OK
```

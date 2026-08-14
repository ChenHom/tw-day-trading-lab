# Price Action P2 Design — MarketTick → canonical 1m MarketBar

Date: 2026-08-14

P2 的唯一目標：

```text
MarketTick
    ↓
1-minute time bucket
    ↓
OHLCV aggregation
    ↓
canonical 1m MarketBar
```

輸入只有 P1 的 `MarketTick`，不需要 Shioaji。實作在 `src/tw_day_trading_lab/bars.py`，該模組只 import stdlib 與 `MarketTick`。

## MarketBar Contract

| 欄位 | 說明 |
|---|---|
| `symbol` | 股票代號 |
| `timeframe` | 第一版固定 `1m` |
| `start_at` | bucket 起點 ISO 字串 |
| `end_at` | bucket 終點 ISO 字串（不含） |
| `open` / `high` / `low` / `close` | OHLC |
| `volume` | 該分鐘 `trade_volume` 加總，單位股 |
| `trade_count` | 該分鐘 tick 筆數 |
| `status` | `OPEN` / `CLOSED` / `CORRECTED` |
| `revision` | 從 1 起算，每次 correction +1 |
| `source` | 第一版固定 `shioaji_tick_aggregated` |
| `sequence_gap` | 該 bar 期間是否出現 P1 sequence 斷號 |

`MarketBar` 是 frozen dataclass。已發出的 revision 物件永遠不會被改寫，所以策略記下的 `bar_revision_used` 天然成立——correction 產生的是新物件，不是就地修改。

`sequence_gap` 超出原始 contract 清單，是刻意加的：斷號是**逐 bar** 的資訊，只記在 aggregator 統計裡的話，下游無法分辨哪一根 bar 可疑。

## Time Bucket

半開區間，交易所本地時間：

```text
[09:00:00, 09:01:00)
[09:01:00, 09:02:00)
```

`09:00:59` 屬於 09:00 bar，`09:01:00` 開始新 bucket。

## 事件時間，不是牆上時間

watermark 只由 tick timestamp 推進，不讀系統時鐘。這是「live 與 replay 共用同一套 aggregator」的必要條件——replay 同一份 tick 序列必然得到位元相同的 bar。

盤中若某段時間完全沒有成交、bar 需要照樣收掉，由呼叫端用自己的時鐘呼叫 `flush(now=...)`。

## Volume

只加 `trade_volume`：

```python
bar.volume += tick.trade_volume
```

`cumulative_volume` 只用於資料完整性檢查（P1 的 `volume_checks`、P2 的 `check_bar_volume`），絕不進入 bar 聚合。

## Missing Minute — 不補 synthetic bar

**沒有成交的分鐘不產生 MarketBar。** 這推翻了 `docs/price-action-intraday-plan.md` 原本「用 previous close 補空棒」的寫法。

原因是這三件事必須保持可分辨：

| 事實 | 表現 |
|---|---|
| 真的成交量 0（有成交，量為 0） | 有 bar，`volume=0`、`trade_count>0` |
| 根本沒有交易 | 沒有 bar；`stats.no_trade_minutes` 計數 |
| feed 漏資料 | `market_data.py` 的 health 轉為 `DEGRADED` / `FAILED` |

補空棒會把前兩者混成同一種 bar，第三者則會被偽裝成「市場很安靜」。要不要補空棒，交給下游 normalization layer 依用途決定：P3 的 5m bucket 與 P5 的 TOD-RVOL 對缺口的處理方式不見得相同。

`MarketBar` 因此沒有 `is_synthetic` 欄位。

## Late Tick

分鐘結束不等於 bar 立刻鎖死。

```text
09:01:00  分鐘結束，bar 仍 pending
09:01:03  watermark 越過 end + lateness → 發出 CLOSED rev=1
```

第一版 `lateness_seconds = 3`。

- **窗內遲到**：09:00:59.8 的 tick 在 09:01:01 抵達 → 直接進 09:00 bar，不產生 correction。
- **窗外遲到**：同一筆 tick 在 09:01:05 之後抵達 → 該 bar 已 CLOSED，改發 `CORRECTED rev=2`。先前發出的 rev=1 物件不變。
- **超出 correction window**（預設 10 分鐘）：計入 `dropped_late` 並丟棄，不靜默套用。

一個 symbol 同時可以有多個 pending bar（09:00 等遲到、09:01 進行中），這是 lateness window 的必然結果。

## Data Quality Counters

`stats()` 輸出：`ticks` / `bars_closed` / `bars_corrected` / `late_ticks` / `dropped_late` / `sequence_gaps` / `no_trade_minutes` / `invalid_timestamps` / `pending_bars` / `watermark`。

`invalid_timestamps`：無法解析的 timestamp 計數後丟棄，不建立 bar、不拋例外。

## Replay

`market_data.replay_raw_ticks(path)` 從 P1 寫下的 `data/raw/shioaji/ticks/{date}/{symbol}.jsonl` 重建 `MarketTick`，套用**與 live stream 完全相同**的過濾、per-symbol sequence 規則與 dedupe key，因此重播一場 session 會得到與當時一模一樣的 MarketTick 序列。

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli bars build-1m \
  --date 2026-08-17 --symbols 2330 --cache-dir data/raw
```

輸出 `reports/{date}-1m-bars.json`，含 `sources` / `aggregator` stats / `volume_check` / `bars`。

`latest_bars()` 把 emission log 收斂成每根 bar 的最新 revision；任何要加總 bar 的地方都必須先過這一層，否則被 correct 過的分鐘會被算兩次。

## P2 Definition of Done — 兩段驗收

### Gate A：P2_CODE_COMPLETE（已達成）

```text
[x] MarketBar contract fixed
[x] 1m bucket 半開區間，跨分鐘正確 rollover
[x] OHLCV 正確（open/close 依事件時間，不依抵達順序）
[x] volume 只加 trade_volume
[x] 多股票各自維護 bar state
[x] missing minute 不補 synthetic bar，缺口有計數
[x] late tick：窗內併入、窗外 CORRECTED、超窗 dropped_late
[x] 已發出的 revision 不可變
[x] sequence gap 標記在受影響的 bar 上
[x] invalid timestamp 計數後丟棄，不建立 bar
[x] replay 與 live 共用同一套 aggregator
[x] bar volume 與來源 tick volume 交叉檢查
[x] unit tests 通過（21 tests）

→ P2_CODE_COMPLETE
→ P3 1m -> 5m 可以開始
```

### Gate B：P2_LIVE_VALIDATED（未達成）

```text
[ ] Real Shioaji Tick -> P1 -> P2 端到端跑過
[ ] 自行聚合的 1m 與盤後 provider 1m 比對合理
    - OHLC
    - volume
    - bar count
    - missing minute
```

比對基準來源：FinMind `TaiwanStockPriceMinute`（單位需先驗證，見 P1 design 的 D4）。

**限制：Gate B 通過前，不得宣告 Tick → 1m pipeline 已可用於每日 paper trading。** 這與 P1 Gate B 是同一條線——`P1_LIVE_VALIDATED` 未過，P2 的 live 比對也不可能成立。

## 不屬於 P2

1m → 5m（P3）、persistence 與 bar revision store（P4）、TOD-RVOL（P5）、Swing / 結構（P6）、Breakout / Retest（P7）。

## Verification

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # 228 tests
PYTHONPATH=src python3 -m compileall -q src tests
```

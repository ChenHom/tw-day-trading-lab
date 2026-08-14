# Price Action Intraday Strategy

## Goal

每日以台股候選股進行 Price Action paper trading，
持續累積樣本，驗證策略是否具有正 Expectancy。

不做真實自動下單。

---

## Existing Project

主專案：

- `ChenHom/tw-day-trading-lab`

參考：

- `ChenHom/tw-swing-trading-mvp`

沿用：

- replay
- paper ledger
- daily report
- candidate engine
- trading-day cycle
- FinMind ingestion
- Shioaji adapter boundary

---

## Market Data Architecture

即時：

```text
Shioaji WebSocket
    ↓
Raw Tick
    ↓
1m canonical bar
    ↓
5m bar
    ↓
Price Action Engine
```

歷史：

```text
FinMind
├─ Daily K
└─ Historical 1m
```

---

## Data Storage

Raw Tick：

```text
data/raw/shioaji/ticks/{date}/{symbol}.jsonl
```

Canonical bars：

```text
market_bars
```

Key：

```text
(symbol, timeframe, bar_time)
```

Timeframe：

```text
1m
5m
```

---

## Tick → 1m

固定台灣交易所時間 bucket：

```text
09:00:00~09:00:59
09:01:00~09:01:59
...
```

OHLCV：

```text
open   = first trade
high   = max price
low    = min price
close  = last trade
volume = sum trade_volume
```

沒有成交的分鐘：

```text
使用 previous close 建 synthetic bar
volume = 0
is_synthetic = true
```

---

## 1m → 5m

```text
09:00~09:04 → 09:00 5m
09:05~09:09 → 09:05 5m
```

聚合規則：

```text
open   = first open
high   = max high
low    = min low
close  = last close
volume = sum volume
```

5m 不直接向 provider 取得。

永遠由 canonical 1m 聚合。

---

## Price Action Main Timeframe

### Daily

用途：

- Context
- Swing
- ATR
- Yesterday High / Low
- 日線市場結構

### 5m

主要交易決策週期：

- Breakout
- Failed Breakout
- Retest
- HH / HL / LH / LL
- Trigger
- Structure Break

### 1m

用途：

- 即時資料基礎
- 5m 聚合來源
- 日後精細 execution

第一版不直接用 1m 產生策略訊號。

---

## Features

第一階段：

- Swing High / Low
- HH / HL / LH / LL
- Breakout
- Failed Breakout
- Retest
- Opening Range
- TOD-RVOL
- Cumulative RVOL
- ATR
- Yesterday High / Low

暫時不加入：

- RSI
- MACD
- KD

VWAP 只作輔助環境資訊，不作主要進場訊號。

---

## Volume Model

### TOD-RVOL

比較目前 5 分 K 成交量與過去 N 個交易日相同時段成交量。

```text
TOD-RVOL
=
current_5m_volume
/
historical_same_time_median_volume
```

第一版歷史窗口：

```text
20 trading days
```

建議同時保留：

- Mean
- Median

Median 優先用於避免單一爆量日扭曲基準。

### Cumulative RVOL

比較今日截至目前的累積成交量與過去相同時間點的歷史累積成交量。

```text
Cumulative RVOL
=
today_cumulative_volume
/
historical_same_time_cumulative_median
```

用途：

- TOD-RVOL：判斷目前這根 K 是否異常放量
- Cumulative RVOL：判斷今天整體是否屬於熱門／高參與交易日

---

## Initial Strategy

第一版只研究一種 Setup：

```text
Breakout
→ volume confirmation
→ acceptance
→ retest
→ higher low
→ trigger
→ entry
```

Invalidation：

```text
跌破 Retest Swing Low
```

---

## Strategy State Machine

```text
WAIT_LOCATION
↓
WAIT_BREAKOUT
↓
WAIT_RETEST
↓
WAIT_TRIGGER
↓
POSITION_OPEN
↓
EXIT
```

### WAIT_LOCATION

等待價格到達重要位置：

- Yesterday High
- Yesterday Low
- Daily Swing High
- Daily Swing Low
- Opening Range High / Low

### WAIT_BREAKOUT

確認 5m 有效突破重要價位，並檢查：

- Close position
- TOD-RVOL
- Cumulative RVOL
- 是否快速收回突破區

### WAIT_RETEST

突破後等待回測。

不直接追突破。

### WAIT_TRIGGER

Retest 成功後：

- 形成 Higher Low
- 突破 Retest 後局部 Swing High

才產生 Entry Trigger。

### POSITION_OPEN

持倉期間持續監控：

- Retest Swing Low
- 5m 結構
- Risk / Reward
- Time Stop
- 收盤前強制退出規則

---

## Decision Flow

```text
Universe Filter
↓
Market Context
↓
Location
↓
Participation
↓
Price Action Setup
↓
Trigger
↓
Risk Model
↓
Paper Trade
↓
Exit
↓
Daily Review
```

---

## Market Data Pipeline

```text
Shioaji Adapter
↓
Normalized MarketTick
↓
Raw Tick Store
↓
1m Bar Aggregator
↓
1m Canonical Store
↓
5m Aggregator
↓
5m Canonical Store
↓
Feature Engine
↓
Price Action State Machine
↓
Decision Table
↓
Paper Ledger
```

Strategy Engine 不直接依賴 Shioaji SDK。

Provider payload 必須先 normalize。

---

## MarketTick Contract

```text
MarketTick
---------
symbol
exchange
timestamp
price
trade_volume
cumulative_volume
source
sequence
```

注意：

- `trade_volume`：單筆成交增量
- `cumulative_volume`：當日累積成交量
- K 棒聚合必須使用 `trade_volume`

不可把 cumulative volume 直接相加。

---

## MarketBar Contract

```text
MarketBar
---------
symbol
timeframe
start_at
end_at
open
high
low
close
volume
trade_count
is_synthetic
status
revision
source
```

Status：

```text
OPEN
CLOSED
CORRECTED
```

Source 範例：

```text
1m:
shioaji_tick_aggregated

5m:
local_1m_aggregated

historical:
finmind
```

---

## Missing Minute Policy

若某分鐘完全沒有成交：

```text
open   = previous close
high   = previous close
low    = previous close
close  = previous close
volume = 0
is_synthetic = true
```

不可直接跳過該分鐘。

否則 5m bucket 與歷史同時段統計會失真。

---

## Late Tick Policy

避免 Bar 一到整分就立即永久鎖死。

第一版可設定：

```text
lateness_window = 3~5 seconds
```

超過 lateness window 才 finalize。

若更晚才收到：

```text
late_tick
```

應保存，但不可靜默修改已經觸發交易決策的 historical live bar。

盤後可產生 corrected revision，但必須保留當時策略實際使用的 revision。

---

## Opening Range

第一版：

```text
09:00~09:15
```

計算：

- Opening Range High
- Opening Range Low

用途：

- Location
- Breakout reference
- 盤中結構判斷

不採用：

```text
突破 OR High → 直接買
```

仍必須進入完整 Price Action state machine。

---

## Daily Context

盤前至少準備：

- Yesterday High
- Yesterday Low
- Yesterday Close
- Daily Swing High
- Daily Swing Low
- ATR14
- Daily Volume MA20
- Daily structure
- Gap

日 K 主要來源：

```text
FinMind TaiwanStockPrice
```

---

## Intraday Historical Baseline

過去至少：

```text
20~30 trading days
```

每個 candidate 保存 1m historical bars。

用途：

- TOD-RVOL
- Cumulative RVOL
- Replay
- Intraday volatility baseline

歷史分 K 可由 FinMind 補齊。

盤中即時策略不可依賴 FinMind polling。

---

## Provider Responsibilities

### Shioaji

用途：

- 即時 Tick
- Streaming market data
- 未來 execution boundary

禁止：

- 盤中反覆 polling `kbars` 掃全市場
- Strategy Engine 直接依賴 Shioaji SDK

### FinMind

用途：

- Daily K
- Historical 1m
- 盤後補資料
- 歷史研究

禁止：

- 當成盤中即時進出場資料源

### Fugle

用途：

- 第二資料來源
- 盤後驗證
- Missing-data fallback

第一版不作主要即時資料來源。

---

## Storage Layers

### Raw

```text
data/raw/shioaji/ticks/{date}/{symbol}.jsonl
```

用途：

- audit
- rebuild
- debug

### Canonical

建議使用 DB：

```text
market_bars
```

Unique Key：

```text
(symbol, timeframe, bar_time)
```

用途：

- strategy
- replay
- feature calculation

### Hot Memory

盤中保留：

- current open 1m bar
- current open 5m bar
- 最近 N 根 1m
- 最近 N 根 5m

Strategy 不需要每次從 DB 重新查最近 K 棒。

---

## Daily Operating Flow

| 時間 | 動作 |
|---|---|
| 盤前 | 載入歷史日 K |
| 盤前 | 載入過去 20~30 日 1m |
| 盤前 | 算 Swing / ATR / Yesterday H/L |
| 盤前 | 產生 candidate list |
| 開盤前 | 訂閱 candidate Shioaji Tick |
| 09:00~13:30 | Tick streaming |
| 每分鐘 | Tick → 1m |
| 每 5 分鐘 | 1m → 5m |
| 每 5 分鐘 | Feature Engine |
| 每 5 分鐘 | Price Action Decision |
| 13:20 後 | 禁止新進場 |
| 收盤前 | Force Exit policy |
| 收盤後 | 補正式日 K |
| 收盤後 | 補／校驗 1m |
| 收盤後 | Replay + P&L |
| 收盤後 | Daily Report |
| 收盤後 | 更新策略樣本 |

---

## Research Rule

每次加入 feature 必須做 ablation test。

比較：

```text
Breakout
```

vs

```text
Breakout + RVOL
```

vs

```text
Breakout + RVOL + Retest
```

vs

```text
Breakout + RVOL + Retest + Trigger
```

最後使用：

- net Expectancy
- Profit Factor
- Win Rate
- MFE
- MAE
- Trade Count
- Drawdown

判斷 feature 是否真的增加 edge。

不能因為回測結果變漂亮就直接保留。

---

## Initial Parameters

第一版只作為研究起點，不視為最佳參數。

| 項目 | 初始值 |
|---|---|
| 主要盤中 K | 5m |
| 原始 canonical K | 1m |
| TOD-RVOL lookback | 20 trading days |
| Breakout TOD-RVOL | >= 1.5 |
| Strong RVOL | >= 2.0 |
| Cumulative RVOL | >= 1.5 |
| Swing | 左右各 2 根 |
| Opening Range | 15 分鐘 |
| Retest Window | 1~6 根 5m |
| Trigger | Retest 後突破局部 Swing High |
| Stop | Retest Swing Low 下方 |
| Exit | Structure / R / Time |

---

## Current Implementation Priority

### P1

建立：

```text
Shioaji Tick
→ normalized MarketTick
```

要求：

- provider adapter
- normalization
- unit tests
- 不做真實下單

### P2

建立：

```text
MarketTick
→ 1m Bar Aggregator
```

要求：

- fixed time bucket
- OHLCV
- synthetic minute
- late tick policy
- BarClosed event

### P3

建立：

```text
1m
→ 5m Aggregator
```

要求：

- 5m 只能由 canonical 1m 產生
- 不直接使用 provider 5m 作策略來源

### P4

建立：

- persistence
- replay contract
- bar revision contract

### P5

建立：

- TOD-RVOL
- Cumulative RVOL

### P6

建立：

- Swing High / Low
- HH / HL / LH / LL
- Structure Break

### P7

建立：

- Breakout
- Failed Breakout
- Retest
- Breakout-Retest-Continuation state machine

### P8

接入：

- paper trading daily cycle
- risk model
- exit model

### P9

建立：

- daily performance report
- expectancy report
- ablation comparison

---

## Codex Handoff

Codex 進入 repo 後第一個任務：

```text
先閱讀：

AGENTS.md
README.md
docs/data-contracts.md
docs/finmind-ingestion.md
docs/trading-day-autonomous-cycle.md
docs/price-action-intraday-plan.md

理解目前 tw-day-trading-lab 的架構後，不要立即修改程式碼。

先檢查目前行情 ingestion、intraday bars、trading-day-cycle、
replay 與 paper ledger 的實作。

找出完成 P1「Shioaji Tick → normalized MarketTick」
需要修改與新增的檔案。

輸出：

1. 現有可沿用元件
2. 缺少元件
3. Data Contract
4. 資料流
5. 實作順序
6. 測試案例
7. 不應修改的既有邊界

這階段不要實作。
```

完成設計確認後，再進入 P1 實作。

# Price Action P3 Design — canonical 1m → canonical 5m

Date: 2026-08-16

實作在同一個 `bars.py`：`FiveMinuteBarAggregator`、`aggregate_1m_to_5m`。

一句話定義：**能把 P2 最新版本的 canonical 1m，依固定時間區間，穩定且可修正地轉成 canonical 5m。**

```text
canonical 1m
    ↓
固定時間 bucket
    ↓
取每分鐘最新 revision
    ↓
重算 OHLCV
    ↓
canonical 5m
```

## Bucket

半開區間，與 1m 同一套規則：

```text
[09:00, 09:05)
[09:05, 09:10)
```

`09:04` 屬於 09:00 bucket，`09:05` 開始新 bucket。

## 聚合規則

```text
open   = 最早 component 的 open
high   = max(component high)
low    = min(component low)
close  = 最晚 component 的 close
volume = sum(component volume)
```

`timeframe = "5m"`，`source = "local_1m_aggregated"`。**5m 永遠不向 provider 取得**；`on_bar` 只接受 `timeframe == "1m"`，其他一律計入 `ignored_timeframe` 並忽略。

## 三個必須成立的性質

**1. bucket 是時間區間，不是「五根 1m」。** P2 對沒成交的分鐘不發 bar，所以 5m 可能只由 4 根、1 根組成。不補假資料、不因不足五根就不產生 5m、不把下一個 bucket 的分鐘借過來湊數。

**2. 修正是 replace，不是 accumulate。** 收到 `09:03 rev2` 時，該分鐘的舊 revision 被整個換掉，然後**重算整根 5m**。不可能出現 `1000 + 1500 = 2500`。低於現有 revision 的 bar 計入 `stale_revisions` 並忽略。

**3. 已發出的 revision 不可變。** `MarketBar` 是 frozen；correction 產生新物件，策略當時看到的那一版原封不動。

bucket 尚未 close 前收到修正，只是換掉 component，不會多算一個 revision——close 時仍是 rev1。

## 生命週期

- watermark 由 1m bar 的 `start_at` 推進（事件時間，不讀時鐘）。
- bucket 在 `bucket_end <= watermark` 時 close，發出 `CLOSED` rev=1。
- close 之後的 component 變更 → `CORRECTED`，revision +1。
- 超出 `correction_window_minutes`（預設 30）的遲到 1m → `dropped_late`，丟棄。
- 盤中無成交仍要收 bucket 時，呼叫端用 `flush(now=...)` 提供時鐘；`close_all()` 收盤用。

`latest_bars()` 的 key 已含 `timeframe`，所以 1m 與 5m 可以放在同一個 list 收斂。

## P3 Definition of Done

### Gate A：P3_CODE_COMPLETE（已達成）

```text
[x] 5m bucket boundary 正確            09:04 -> 09:00 / 09:05 -> 09:05
[x] OHLCV aggregation 正確             O/H/L/C/V 與 component 完全一致
[x] missing 1m 不影響 bucket 定義       不補 synthetic、不借下一個 bucket
[x] 1m correction 能 replace + 重算 5m  revision +1、不重複加總、舊版本不變
[x] identical input -> identical latest 5m output   同一 event log 重跑三次相同

→ P3_CODE_COMPLETE（20 tests）
```

### Gate B：P3_LIVE_VALIDATED（已達成，2026-08-17）

```text
[x] Real Shioaji Tick -> P1 -> P2 -> P3 端到端
[x] 自行聚合的 5m 與盤後 provider 5m 比對合理
```

2026-08-17 端到端產出 95 根 5m，無 correction、stale 或 dropped。2026-08-18 比對時，13:20 bucket 兩邊完全相等；provider 把收盤集合競價放進 13:25 bucket，我方放在 13:30，該筆成交量相等，屬標記位置差異而非資料差異。paper 在 13:25 強制平倉，不受影響。證據見 `docs/development-work.md`。

## P3 刻意不做

`component_bar_count` / `missing_minutes` 欄位、`bars build-5m` CLI、5m 專屬 metrics。這些是品質資訊或便利性，拿掉不影響 P3 成立，之後要補很便宜。

`trade_count` 與 `sequence_gap` 有做，因為 `MarketBar` 是 1m / 5m 共用的 dataclass，這兩個欄位一定得有值；填正確值各只要一行，填 `False` 反而是說謊。

## Verification

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # 228 tests
PYTHONPATH=src python3 -m compileall -q src tests
```

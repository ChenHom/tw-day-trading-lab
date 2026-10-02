# Price Action P6–P8 Design — Structure, Setup, Paper Trading

Date: 2026-08-16

三個階段一起交付，因為它們是同一條鏈：

```text
canonical 5m
    ↓  PA-P6  structure.py
Swing / HH-HL-LH-LL / BOS
    ↓  PA-P7  setup.py
Breakout → Retest → Trigger → SIGNAL
    ↓  PA-P8  paper.py
Paper Trade lifecycle
```

模組相依是單向的，且都不碰 Shioaji：

```text
bars ← structure ← setup ← paper
                     ↑
                   rvol
```

---

# PA-P6 — Swing / Market Structure

一句話定義：**給一段 canonical 5m，可以穩定輸出 Swing 與目前市場結構。**

## Swing 判定

```text
Swing High: high 嚴格大於 前 N 根與後 N 根的 high
Swing Low:  low  嚴格小於 前 N 根與後 N 根的 low
```

第一版 `N = 2`。

兩個刻意的決定：

- **必須兩側都有 N 根才算確認**，所以最新的 N 根**不會**是 swing。當下那根就宣告是 swing 會讓結構隨雜訊翻轉，而且 live 與 replay 會不一致。
- **嚴格不等**：相等的鄰居不算 swing，結果不依賴任何 tie-break 規則。

「missing bar 不會被當成價格 0」在這裡是結構性成立的：輸入是 `MarketBar` 物件，PA-P2 對無成交分鐘不發 bar，所以鄰居是**清單上的鄰居 bar**，不是時鐘上的鄰近 bucket，沒有任何地方會出現 0。

## 結構分類

比較最後兩個 swing high 與最後兩個 swing low：

| 條件 | structure | trend |
|---|---|---|
| HH + HL | `HH_HL` | `UP` |
| LH + LL | `LH_LL` | `DOWN` |
| 混合 | `MIXED` | `RANGE` |
| swing 不足 2 個 | `UNKNOWN` | `UNKNOWN` |

## BOS

```text
close > last_swing_high → bullish
close < last_swing_low  → bearish
否則 none
```

## PA-P6 Definition of Done

```text
[x] Swing High 判定 deterministic
[x] Swing Low 判定 deterministic
[x] HH / HL / LH / LL 正確分類
[x] bullish / bearish BOS 有明確規則
[x] correction 後 structure 可以重算
[x] missing bar 不會被當成價格 0
[x] 同一段 5m replay 結果一致

→ PA-P6_CODE_COMPLETE（16 tests）
```

`compute_structure` 是純函式，餵入修正後的 bar 就會重算，不需要額外機制。

---

# PA-P7 — Breakout → Retest → Trigger

一句話定義：**給一串 5m Bar + RVOL + Structure，可以 deterministic 地回答 Buy / Wait / Invalidated。**

```text
WAIT_BREAKOUT → WAIT_RETEST → WAIT_TRIGGER → SIGNAL
                      |              |
                      +→ INVALIDATED ←+
```

不做 scoring，每個轉換只有一條明確條件。

| 轉換 | 條件 |
|---|---|
| Breakout | `close > 已確認的 swing high` **且** `TOD-RVOL >= 1.5` |
| Retest | `low <= breakout_level × (1 + tolerance)` |
| Trigger | `close > retest 後形成的 local high` |
| Invalidated | `close < level × (1 - tolerance)`（有效跌破）／ `low < retest_low` ／ retest window 逾期 |

Stop = retest low，Target = entry + 2R。**entry / stop / target 在 signal 當下就全部確定**，沒有事後才決定的部分。

第一版參數：`tolerance = 0.003`、`retest_window = 6` 根、`target_r = 2.0`。

## 兩個關鍵決定

**RVOL 缺失或 insufficient 一律不 breakout。** 「資料不知道」不等於「通過 volume gate」。`rvol is None`、`status != ok`、`tod_rvol is None` 三種情況都直接不成立。

**同一個 breakout level 每個 symbol 只用一次。** 進場後價格仍在該 swing high 之上，不擋的話下一根會用同一個 level 再 breakout 一次。`used_levels` 記錄已用過的價位。

`setup_id = {date}:{symbol}:{breakout_bar_start_at}:brk`，由輸入決定，因此 replay 得到相同 id。

## PA-P7 Definition of Done

```text
[x] 沒 breakout 不會進 retest
[x] breakout 必須突破有效 Swing level
[x] volume gate 正確套用
[x] retest 有明確價格區間
[x] retest 失敗會 invalidated
[x] trigger 發生才產生 entry signal
[x] stop price 可以明確算出
[x] 每個 setup 有唯一 setup_id
[x] 同一事件 replay 不會重複產生 signal

→ PA-P7_CODE_COMPLETE
```

---

# PA-P8 — Paper Trading Daily Cycle

一句話定義：**拿一整天歷史資料 replay，可以完整跑完「找 setup → 進場 → 管理 → 出場」，且每次重跑結果完全一致。**

只寫 paper ledger，不碰 broker、不下單、不取消。

## 四條不可妥協的規則

**1. 同一根 bar 內先判 stop 再判 target。** 一根 5m 同時涵蓋兩個價位時是模稜兩可的；假設好結果會在**最該保守的高波動 bar 上**灌水 expectancy。

**2. 一個 setup_id 永遠只成交一次。** 用既有的 `PaperLedger`，且**不釋放** key，所以 replay 或 restart 都不可能重新進場同一個 setup。

**3. market data 不健康時禁止新進場。** PA-P1 回報 health；用漏了 tick 的 feed 算出來的訊號不算訊號。既有部位仍照常管理與強制平倉。

**4. 收盤前一定清倉。** 到 `force_exit_at` 強制平倉；跑完所有 bar 後若還有部位，在最後一根平掉，理由 `pre_close`。

## 風控

| 項目 | 第一版 |
|---|---|
| 每日最多新進場 | 2 |
| 同一 symbol 同時部位 | 1 |
| 停止新進場 | 13:20 |
| 強制平倉 | 13:25 |

出場理由：`stop` / `target_2r` / `force_exit` / `pre_close`。

每筆交易保存：`setup_id` / `symbol` / `entry_time` / `entry_price` / `stop_price` / `target_price` / `exit_time` / `exit_price` / `exit_reason` / `r`。

被擋下的訊號也保存於 `skipped[]`，理由包含 `market_data_unhealthy` / `after_hard_stop` / `max_new_entries` / `position_already_open` / `duplicate_setup`，否則無法分辨「策略沒觸發」與「被風控擋掉」。

## PA-P8 Definition of Done

```text
[x] 一個 trading day 可以從開盤跑到收盤
[x] P1~P7 可以完整串起來
[x] Signal 不會重複開倉
[x] 同一 symbol 同時只有合理數量的 position
[x] 每日最多 2 個新進場
[x] entry / stop / target 都有記錄
[x] stop hit 能平倉
[x] 2R hit 能平倉
[x] pre-close 一定清倉
[x] 每筆交易都有完整 lifecycle
[x] restart / replay 不會重複成交
[x] market data unhealthy 時禁止新進場

→ PA-P8_CODE_COMPLETE（23 tests，含 PA-P7）
```

## CLI

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli paper run-day \
  --date 2026-08-17 --symbols 2330 --store-dir data/bars
```

從 bar store 讀歷史算 RVOL baseline，讀當日 5m，跑完整天，輸出 `reports/{date}-paper.json`。

---

## 三個階段共同的 Gate B

```text
[x] PA-P7/P8_LIVE_VALIDATED（2026-08-19）
```

2026-08-19 第一次完整場次（12 檔、08:58 由 cron 啟動收至 13:31）中，SIGNAL → entry → stop → exit 的完整鏈在當日 tick 聚合出的 bar 上跑完，`market_data_healthy=true` 取自當日 session report 而非人工旗標。PA-P6 的 swing 是這條鏈的輸入，但 `docs/development-work.md` 沒有另外宣告 `PA-P6_LIVE_VALIDATED`。

**Gate B 驗證的是機制會動，不是機制會賺。** 當日兩筆皆停損（`total_r = -2.00`）。後續 PA-P9 接上成本後的研究結論見 `docs/development-work.md` 2026-08-19 (3) 起各節與 2026-08-22「方向翻轉」：突破訊號 alpha 約為零，且引擎只做多。

## 不屬於 P6–P8

setup scoring、多空雙向（目前只做多）、部位大小 / 資金管理。ablation 比較、expectancy 報表與成本 / 滑價套用屬於 PA-P9，已於 2026-08-19 完成。

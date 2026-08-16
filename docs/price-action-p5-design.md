# Price Action P5 Design — TOD-RVOL / Cumulative RVOL

Date: 2026-08-16

一句話定義：**給一根今天的 5m Bar，可以可靠回答「這個時間點的量，相較正常水準放大幾倍」。**

```text
TOD-RVOL = 這根 5m 的 volume      / median(過去 N 日同一時段的 volume)
Cum-RVOL = 今天截至目前累積 volume / median(過去 N 日同一時段的累積 volume)
```

第一版 `N = 20` 個交易日，baseline 取 **median**（同時保留 mean 供對照）。

實作在 `src/tw_day_trading_lab/rvol.py`，輸入只有 canonical `MarketBar`。

## 資料源決定（重要）

**FinMind 分 K 拿不到。** 2026-08-16 實測 `https://api.finmindtrade.com/api/v4/data`：

```text
TaiwanStockPrice  → HTTP 200  success
TaiwanStockKBar   → HTTP 400  Your level is free. Please update your user level.
```

`TaiwanStockKBar` 需付費 sponsor 等級。因此 baseline 來源改用 **Shioaji `api.kbars(contract, start, end)` 盤前 backfill**，實作在 `src/tw_day_trading_lab/backfill.py`。

日 K `TaiwanStockPrice` 不受影響，匿名即可取得，且已用算術確認 `Trading_Volume` 單位是**股**（`Trading_money / Trading_Volume` 落在當日價格區間內）。這個已確認的數字正是驗證 kbar 單位的基準，見下方。

## 邊界

AGENTS.md 禁止的是「盤中反覆 polling kbars 掃全市場」。這裡做的是不同的事：

- **盤前**、**有界**、**只針對已產生的候選名單**的歷史抓取。
- 預設關閉，必須帶 `--enable-kbars-backfill` 才會登入。
- 只登入行情：`fetch_contract=True`、`subscribe_trade=False`，且拒絕 `simulation != True`。
- 不下單、不取消。`checks.orders_allowed` 與 `checks.intraday_polling` 固定為 false。

backfill 產出的 bar 標 `source="shioaji_kbars"`，與自行聚合的 `shioaji_tick_aggregated` 永遠可區分。

## kbar volume 單位未查證

Shioaji `Kbars` 是欄狀物件（`ts` 奈秒 / `Open` / `High` / `Low` / `Close` / `Volume` / `Amount`），SDK **沒有**標註 `Volume` 的單位。

處置：預設 `volume_in_lots=True`（與已查證的 tick 單位一致，乘 1000 轉股），並提供 `check_backfill_against_daily()` 用已確認單位的日 K 定案：

```text
ratio = 一日 backfill 分 K volume 總和 / 該日 TaiwanStockPrice.Trading_Volume

ratio ≈ 1      → 換算正確
ratio ≈ 1000   → 多乘了，應改 --volume-in-shares
ratio ≈ 0.001  → 少乘了
```

盤中 backfill 不含收盤集合競價，所以不要求完全相等；這個檢查抓的是**數量級**錯誤。

`ts` 的時區換算（`datetime.fromtimestamp`，跟隨系統時區）同樣未實測。`session_complete.by_day` 會回報每日 bar 數，完整場次應為 270 根（09:00–13:30），數字明顯不對就是時區或截斷問題。

## Baseline 規則

- **按相同 time slot 比較**：`start_at[11:16]`，例如 `09:30`。
- **缺 bar 的日子不算 0**。PA-P2 對無成交分鐘不發 bar，若把缺席當成 0，baseline 會被拉低並製造假的量能突破。該日單純不貢獻該 slot 的樣本，`SlotStat.days` 記錄實際貢獻天數。
- **median 優先**：單一爆量日不應移動基準。mean 同時保留，僅供對照。
- 只取最近 `lookback_days` 個不同交易日。

## insufficient_data 是 fail closed

`sample_days < min_days` 時：

```text
status   = "insufficient_data"
tod_rvol = None
cum_rvol = None
```

**刻意不回傳比值。** 回傳一個數字，就會有人拿去交易。baseline 與 `sample_days` 仍在結果裡供檢查。未知的 slot（例如 baseline 沒有 13:15）同樣是 `insufficient_data`，不是例外。

baseline median 為 0 時比值也回 `None`，不做除以零。

## CLI

```bash
# 盤前 backfill（預設 blocked）
PYTHONPATH=src python3 -m tw_day_trading_lab.cli bars backfill-kbars \
  --start-date 2026-07-01 --end-date 2026-07-31 \
  --candidates-input reports/2026-08-17-candidates.json \
  --store-dir data/bars --enable-kbars-backfill

# 用 store 裡的歷史算今日 RVOL
PYTHONPATH=src python3 -m tw_day_trading_lab.cli bars rvol \
  --date 2026-08-17 --symbols 2330 --store-dir data/bars
```

`bars rvol` 只讀 `data/bars/{timeframe}/{date}/`，歷史日期取小於 `--date` 的最近 `--lookback-days` 天。

## P5 Definition of Done

### Gate A：PA-P5_CODE_COMPLETE（已達成）

```text
[x] 能建立至少 20 個交易日 baseline
[x] baseline 必須按相同 time slot 比較
[x] TOD-RVOL 計算正確
[x] Cum-RVOL 計算正確
[x] 缺歷史資料時明確標記 insufficient_data
[x] missing 1m / 5m 不可偷偷補成 0
[x] replay 同一天資料結果 deterministic

→ PA-P5_CODE_COMPLETE（21 tests）
```

### Gate B：PA-P5_LIVE_VALIDATED（未達成）

```text
[ ] 真實 kbars backfill 跑過，simulation 帳號確認取得得到分 K
[ ] check_backfill_against_daily 的 ratio 落在 0.8–1.2（單位定案）
[ ] session_complete 每日 bar 數合理（完整場次 270）
[ ] 20 個交易日 backfill 完成，baseline_days >= 20
```

前兩項未過之前，`bars rvol` 的輸出只是演算法正確，不代表數值可信。

## 不屬於 P5

RSI / MACD / KD、多版本 baseline、依星期或波動分群的 baseline、RVOL 的自動門檻調參。第一版門檻（breakout >= 1.5、strong >= 2.0、cum >= 1.5）留到 PA-P7 使用時再套。

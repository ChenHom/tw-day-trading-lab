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

## kbar timestamp — 已修正的實際 bug

Shioaji 的 `ts` 把**交易所本地時間當成 naive UTC epoch** 編碼。官方 2330 範例 `ts = 1779094860000000000` 顯示為 `2026-05-18 09:01`：

```text
datetime.fromtimestamp(sec)             → 17:01   ← 錯，且隨機器時區而異
datetime.fromtimestamp(sec, tz=utc)     → 09:01   ← 正確
pandas.to_datetime(ts)                  → 09:01   ← Shioaji 自己的範例用這個
```

初版用了本地時區版本，在 UTC+8 的機器上會讓每一根 backfill bar 整體偏移 8 小時，**PA-P5 的所有 time slot 會全錯**，而且換一台時區不同的機器結果還會不一樣。已改為 UTC 解碼後取 naive 值，並用官方實際數值寫了測試。

原本的測試 fixture 也用本地時區編碼 `ts`，正是這個編碼錯誤讓 bug 沒被抓到；fixture 已改成與 Shioaji 相同的編碼方式。

## kbar bar 標記 — 第二個已修正的實際 bug

**Shioaji kbars 用 bar 結束的分鐘標記,canonical MarketBar 用開始的分鐘。**

2026-08-17 實跑 2330 於 2026-08-14 的證據:

```text
標記 09:01 的 bar → open = 2435.0 = 當日開盤價（09:00:00 成交）
                  → 實際涵蓋 09:00:00–09:00:59
最後一根標記 13:30 → close = 2395.0 = 當日收盤價
一天 266 根：09:00–13:24 連續 265 根 + 收盤集合競價 1 根
```

未修正時每根 backfill bar 都比實際晚 1 分鐘,5m 聚合會有一根落到錯的 bucket,PA-P5 baseline 建在偏移的 slot 上再與正確標記的 live bar 比較。已改為解碼後減 1 分鐘。

已知且有界的例外:收盤集合競價那根(標記 13:30,實際涵蓋 13:25–13:30)會落在 13:29。它與 13:25 屬於同一個 5m bucket,對 5m 無影響。

`FULL_SESSION_MINUTES` 也由 270 改為 **266**。原本的 270 會讓每一天都被標成 `short_day`,真正被截斷的抓取反而淹沒在雜訊裡。

## kbar volume 單位

SDK 沒有標註，但官方 2330 範例在算術上是決定性的：

```text
Volume 2565 @ close 2230
當成張：2565 × 1000 × 2230 ≈ 5,719,950,000   ≈ 官方 Amount 5,708,965,000（差 0.19%）
當成股：2565 × 2230        ≈ 5,719,950       差 1000 倍
```

因此 `volume_in_lots=True`（乘 1000 轉股）是對的，與已查證的 tick 單位一致。仍保留 `check_backfill_against_daily()` 用已確認單位的日 K 做實證定案：

```text
ratio = 一日 backfill 分 K volume 總和 / 該日 TaiwanStockPrice.Trading_Volume

ratio ≈ 1      → 換算正確
ratio ≈ 1000   → 多乘了，應改 --volume-in-shares
ratio ≈ 0.001  → 少乘了
```

盤中 backfill 不含收盤集合競價，所以不要求完全相等；這個檢查抓的是**數量級**錯誤。

`session_complete.by_day` 會回報每日 bar 數，完整場次應為 270 根（09:00–13:30），數字明顯不對就是截斷問題。

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

### Gate B：PA-P5 backfill 已實測（2026-08-17）

```text
[x] 真實 kbars backfill 跑過，simulation 帳號確實取得得到分 K
[x] volume 單位定案：張。ratio 0.89 / 0.96 / 0.91（2330 / 2317 / 2454），皆在 0.8–1.2
[x] 每日 bar 數 266，與連續交易 265 + 收盤 1 相符
[x] 22 個交易日 backfill 完成
[x] OHLC 與 FinMind 日 K 完全相符（三檔的 open / close / high / low 全對）
[ ] 今日 canonical 5m 的 RVOL 實算（等收盤後的 tick 聚合）
```

日 K 交叉驗證缺的 4–10% 是日 K 含零股 / 盤後 / 鉅額而分 K 不含,屬預期差異。

實測用的區間限制:**Shioaji kbars 單次請求不得超過 30 天**（`Kbars date range must not exceed 30 days.`）。要拿超過 30 天必須自行分段,目前 `backfill.py` 未做分段。

## 不屬於 P5

RSI / MACD / KD、多版本 baseline、依星期或波動分群的 baseline、RVOL 的自動門檻調參。第一版門檻（breakout >= 1.5、strong >= 2.0、cum >= 1.5）留到 PA-P7 使用時再套。

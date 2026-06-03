# 六大層面核心問題審查與自動化營運指南

本文件彙整了針對台股當沖系統在策略 Edge、流動性評估、特徵指標足夠性、分層架構優化以及自動化營運排程上的核心討論與設計共識。

---

## 1. 候選資料生命週期管線 (Candidate Data Pipeline)

本系統的候選資料流是由「盤後資料吸納」至「晨間計畫建立」的三階段自動化管線所構成：

```
+------------------+     15:00     +----------------------+
|  FinMind Ingest  | ------------> | Local raw JSONL cache|
+------------------+               +----------------------+
                                               |
                                               | 15:30 (build-from-raw)
                                               v
+------------------+     08:30     +----------------------+
|   Input Plan     | <------------ | Ranked Watchlist     |
|   (input_plan)   |               | ({date}-candidates)  |
+------------------+               +----------------------+
```

1. **資料吸納 (Ingestion)**：盤後自動請求 FinMind API，下載當日股價、融資融券及法人買賣超數據，並快取儲存於本地 `data/raw/` 目錄下。
2. **候選篩選 (Building)**：由 [candidate_builder.py](file:///home/hom/services/stock/tw-day-trading-lab/src/tw_day_trading_lab/candidate_builder.py) 進行首輪篩選，排除權證、ETF 等非普通股，並要求單日成交額需達 $80,000,000$ 元門檻，計算包含 20 日 ATR 百分比在內的多維特徵。
3. **評分與排序 (Scoring & Ranking)**：[candidate_engine.py](file:///home/hom/services/stock/tw-day-trading-lab/src/tw_day_trading_lab/candidate_engine.py) 依公式進行綜合評分（滿分 100），標記高波動且具備操作性的個股為 `next_day_actionable = True`，輸出成每日候選 JSON 檔。

---

## 2. 流動性判定失真與防禦機制 (Liquidity Distortion)

單純以「單日成交金額」作為唯一流動性度量指標，會面臨以下三大交易失真，系統已針對此實作了對應防禦：

| 失真痛點 | 實戰風險 | 系統現有防禦機制 | 後續優化建議 |
|---|---|---|---|
| **單日新聞暴量** | 假流動性。隔天成交量急速退潮，平倉面臨嚴重滑價或流動性蒸發。 | 計算 `volume_expansion`（當日量 / 5日均量）。若暴增過大，會拉高 `crowding_penalty` 扣分，壓低其排名。 | 盤後 Ingestion 的過濾條件改用 **20日均成交金額 (20-day ADV)**。 |
| **股價階梯價差** | 高價股與低價股一檔（Tick）佔股價比率不同。相同成交額下，高價股買賣價差（Spread）通常較寬。 | 實作台股六級 **Tick Size 動態滑價成本**，高價股會自動在回測與計畫階段扣除更高比例的滑價成本。 | 盤中開倉前，加入個股即時 **Bid-Ask Spread %** 的動態檢查。 |
| **大單市場衝擊** | 忽略了委託數量相對於市場胃納量的比例。 | 實作 Fixed Fractional Position Sizing，限制委託規模。 | 風控層加入 `Order Size / Daily Volume < 1%` 的相對比例門禁。 |

---

## 3. 關鍵指標足夠性與優化方向 (Feature Sufficiency)

目前的指標組合對「夜間篩選 (Screening)」是足夠的，但為獲取穩定的統計優勢（Edge），建議將以下三個進階指標加入「盤中訊號引擎（Signal Engine）」：

1. **大盤環境過濾器 (Market Regime Filter)**：
   - 當大盤 EMA/MA 趨勢偏空，或大盤 5 日 ATR% 過低時，動能突破策略易失效，應主動降倉或暫停交易。
   - 開盤大盤跳空大跌超過 $1.5\%$ 時，屬於高風險流動性蒸發日，應強制暫停當沖。
2. **同族群連動強度 (Sector Relative Strength)**：
   - 突破時需有同產業板塊（如 AI、半導體設備）3 檔以上共振突破，以排除個股假突破。
3. **開盤跳空幅度限制 (Opening Gap Limit)**：
   - 跳空開高過大（例如 Gap > 4%）的股票，極易發生高開低走（衰竭行情），訊號引擎應過濾此類標的。

---

## 4. 分層架構後的進階優化空間 (Post-layering Optimization)

系統模組解耦分層後，可以進一步在以下領域進行深度優化：

1. **執行演算法與滑價補償 (Slippage Feedback)**：
   - 記錄每一筆真實成交的滑價，若近期實際滑價 consistently 大於預估，風控模組自動調降（Downsize）該標的的委託額度。
   - 委託單改用 IOC (Immediate-or-Cancel) 以防價格飛走時以不利價格成交。
2. **投資組合層級風控 (Portfolio Exposure)**：
   - 加入同板塊/同主題概念股的持倉限制（如同一概念股同時持倉不得超過 1 檔），強迫分散相關性風險。
   - 依大盤波動度動態調整單筆風險比例（$0.25\% \sim 1.0\%$）。
3. **數據反饋與特徵歸因 (Attribution & Autotuning)**：
   - 標記每筆交易的環境特徵，每月進行決策樹分析，抓出負 expectancy 的特徵交集並將其設為策略黑名單。
   - 每週自動執行 Walk-Forward 以更新 Screener 的優化參數。

---

## 5. 風控門禁與正確性檢驗 (Gates Correctness Review)

系統的底層運算與防呆邏輯已經過嚴格審查，確認無數學與台股規則偏差：
* **20元最低手續費**：以 `max(comm, 20/qty)` 換算單股成本，確保小金額回測成本不失真。
* **日內 Exits 衝突**：`check_exits` 中**優先執行停損 (`stop_loss`)**，此為回測中最嚴謹、不灌水的前視偏差預防做法。
* **台股微結構防範**：門禁模組硬性阻擋漲停買入、跌停賣出、無券做空，並主動避開 **13:25–13:30 收盤集合競價時段**。
* **信賴區間與樣本警示**：使用標準誤差估算 95% Confidence Interval，並對樣本數低於 30 和 100 的分析發出警示。
* **滾動回饋防禦**：`RollingPerformanceTracker` 監控最近交易，當 expectancy 轉負或 Drawdown 超限時自動調降 Quantity 或完全禁用策略。

---

## 6. 自動化營運排程時序 (Automated Operations Timeline)

在生產環境下，自動化流程按以下時間序由 cron / 排程器啟動：

* **15:00 (T日收盤後)**：FinMind 資料庫更新完畢，自動化 Ingest 啟動：
  ```bash
  PYTHONPATH=src python3 -m tw_day_trading_lab.cli ingest --date $(date +%Y-%m-%d)
  ```
* **15:30 (T日篩選)**：自動計算特徵並生成明日候選清單：
  ```bash
  PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build-from-raw --date $(date +%Y-%m-%d)
  ```
* **08:30 (T+1日開盤前)**：載入昨日候選名單，驗證 7 大 Pre-order 門禁，建立今日下單計畫 `input_plan.json`：
  ```bash
  PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate ops-run \
    --date $(date +%Y-%m-%d) \
    --candidates-input reports/$(date -d "yesterday" +%Y-%m-%d)-candidates.json \
    --simulation-on
  ```
* **13:45 (T+1日收盤後)**：自動匯入本日日誌，比對 callback 狀態，產出 close report，並依據結果自動更新 `RegressionCase`。

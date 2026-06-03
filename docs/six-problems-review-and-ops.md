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

單純以「單日成交金額」作為唯一流動性度量指標，在台股當沖實戰中會產生嚴重的流動性失真問題：

1. **高價低量股的「假流動性」陷阱**：
   - *案例*：一檔股價 1,000 元的個股，單日成交量僅 10 張，其成交金額即高達 10,000,000 元。
   - *風險*：雖符合千萬成交額門檻，但因張數極少，盤中委託簿極度稀疏，買賣五檔價差 (Bid-Ask Spread) 極大。當沖單一筆 (例如 1-2 張) 的送出即可能造成數個 Tick 的滑價，平倉時極易產生難以預估的交易成本甚至無法成交。
2. **低價高量股的「被低估流動性」誤殺**：
   - *案例*：一檔股價 15 元的個股，單日成交量達 500 張，成交金額僅 7,500,000 元。
   - *優勢*：此類股票雖然金額未達 10,000,000 元，但由於張數充足，市場參與者多，盤中委託簿厚實，交易滑價極小，實際上是非常適合當沖小額資金進出的標的。若僅用成交金額初篩，該標的會被無情排除。
3. **單日事件性暴量 (News/Event Inflow)**：
   - *風險*：某些平時無人問津的冷門股因單日消息面刺激爆出巨量，導致單日成交金額暴增，但隔日極易面臨「流動性快速退潮 (Liquidity Fade)」的慘劇，導致進場後無法順利退場。

### 系統現有防禦與多維流動性優化建議

| 流動性失真面向 | 實戰風險 | 系統現有防禦機制 | 後續多維度流動性優化方案 |
|---|---|---|---|
| **單日新聞暴量** | 假流動性。隔天成交量急速退潮，平倉面臨嚴重滑價或流動性蒸發。 | 計算 `volume_expansion`（當日量 / 5日均量）。若暴增過大，會拉高 `crowding_penalty` 扣分，壓低其排名。 | 盤後 Ingestion 與過濾條件應改用 **20 日均成交金額 (20-day ADV)**，以平滑單日異常波動。 |
| **股價階梯價差** | 高價股與低價股一檔（Tick）佔股價比率不同。相同成交額下，高價股買賣價差（Spread）通常較寬。 | 實作台股六級 **Tick Size 動態滑價成本**，高價股會自動在回測與計畫階段扣除更高比例的滑價成本。 | **多維度 Universe 過濾**：除金額外，新增「單日成交張數（Volume）低於 500 張者強制過濾」與「股價高於 500 元個股的 Spread % 檢查」。 |
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
* **08:30 (T+1日開盤前)**：載入昨日候選名單，驗證 7 大 Pre-order 門禁，建立今日下單計畫 `input_plan.json` 並執行模擬下單。現在已簡化為透過 Makefile 一鍵執行：
  ```bash
  make ops-run
  ```
  *(註：此指令會自動讀取 `.env` 與自動加載 `Sinopac.pfx` 憑證，並自動回推 10 天內存在的最新候選檔作為輸入，無需手動填入繁瑣的參數。)*
* **13:45 (T+1日收盤後)**：自動匯入本日日誌，比對 callback 狀態，產出 close report，並依據結果自動更新 `RegressionCase`。

---

## 7. 自動化啟動與防呆運行機制 (Automation Execution & Safeguards)

自動化腳本的背後並非單純的命令串聯，而是配備了嚴格的自動化防呆與狀態同步機制，以確保交易系統的穩定性與可追溯性：

1. **版本一致性防線 (Git Clean Check)**：
   - 自動化流程啟動時，SOP 腳本會先檢查目前工作目錄的 Git 狀態。
   - 若偵測到本地有未 Commit 的程式碼修改，會拒絕執行並發出警訊。此設計是為了避免測試中的程式碼或未經驗證的邏輯在生產環境中意外運行。
2. **防範重複執行與死鎖 (Lock File Mechanism)**：
   - 為防止排程器因超時重複觸發、或前日卡死導致的資源衝突，排程腳本在啟動時會建立一個 PID 鎖定檔（如 `tw_day_trading.lock`）。
   - 若該鎖定檔已存在且對應行程仍在運行，則本次自動化執行將立即中斷並警報。
3. **營運軌跡的可追溯性 (Ops Run Manifest & Checksums)**：
   - 每次自動化執行（即使是完全無人值守的 simulation 或是 pre-order planning）都會生成唯一的 `run_id`。
   - 自動化流程會把輸入資料的 Checksum、Git Commit SHA、指令參數、以及輸出產物路徑與其對應的 Checksum 寫入 `ops_run_manifest`。
   - 這是為了讓後續的 Regression Correction Loop (P10) 能精確定位到具體某天、某個版本的執行細節，實踐端到端的審計能力。
4. **資料完整性門禁 (Data Ingestion Integrity Gates)**：
   - 當 Ingestion 遭遇 FinMind API 斷線、限流、或回傳空資料時，自動化管線會記錄 `data_quality = degraded`。
   - 若關鍵欄位（如昨收價、今日開盤價等）完全缺失，系統會主動阻擋後續的 `ops-run` Plan 建立，防止因髒資料導致錯誤的下單部位與爆倉風險。

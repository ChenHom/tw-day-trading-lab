# MVP Roadmap

本文件的 Phase 命名必須和 `docs/development-work.md` 保持一致。若實作狀態改變，優先同步這兩份既有文件，不另開新的 phase 狀態文件。

## Phase Review Rule

每個 phase / sprint 完成後，必須執行 grill-me review，確認實作方向、驗證範圍與文件狀態沒有偏離。review 結論必須寫回 `docs/development-work.md` 對應 phase；若 roadmap 狀態改變，才同步更新本文件。

P8 之後每個 phase close-out 不能只列「新增命令」或「新增文件」，必須同時通過：

- command gate：有可重跑命令。
- artifact gate：產出可追蹤 artifact / manifest。
- regression gate：失敗情境可轉成測試或 fixture。
- operator gate：報告或 alert 明確告訴操作者下一步。

## Cross-phase Failure Controls

這些控制點橫跨 P0-P11，不屬於單一 phase，但任何後續 phase 都必須遵守。

- 已完成不等於無風險：P0-P7 仍可能有 residual risk，後續 phase 不可假設舊 phase 完美。
- 每個 ops artifact 必須可追溯到 run id / source input / command / checksum，否則 P10 regression 無法成立。
- simulation / readiness / live execution 都不得被當成 strategy edge；strategy edge 只能由合格 replay / research sample 證明。
- readiness report 在 P7 只是 report contract；真正的 live execution enforcement 必須在 P11 broker boundary 再做一次，不可只信 report。
- approval token 不可使用可預測字串作為正式密鑰；P7 的預設 token 只適合測試 gate，不適合正式營運。

## P0: Repo Bootstrap

目前狀態：已完成。

- 建立乾淨 repo。
- 預設分支使用 `master`。
- 建立 README、資料契約、TiDB schema、CLI skeleton。
- 放入 sample candidate fixture。
- 測試候選排序、報告輸出、ledger idempotency。

## P1: Old Log / CSV Importer

目前狀態：已完成 MVP。

- 匯入舊交易 log / CSV。
- 將樣本分成 `valid`、`excluded`、`needs_review`。
- 標記 debug、強制下單、盤後測試、重複開倉、缺 exit、ledger 污染。
- 產生第一版 failure replay report。
- 驗證 paper ledger 的 `idempotency_key` 能重現並擋住雙 `ENTER`。

## P2: TiDB Integration

目前狀態：已完成 MVP。

- TiDB candidate metadata：已可保存 candidate runs / items。
- TiDB classified samples：已可保存 valid / excluded / needs_review，並保留 duplicate ENTER evidence。
- `fetch_ledger` schema 已存在，供 P3 API cache 使用。
- `order_intents.idempotency_key` 是真正的下單冪等性邊界。

## P3: FinMind Nightly Ingestion

目前狀態：已完成 cache / ledger MVP。

- FinMind nightly ingestion：已完成 fetch ledger + raw JSONL cache MVP。
- 同一 dataset/date/stock/source 重跑可 skip，避免重複打 API。
- planned calls 預設上限 540/hr。
- 無 token 不 crash，回報 `auth_missing`。
- 真實 FinMind smoke 已確認第一次 actual=1、第二次 skipped=1。
- 20-50 日窗口已在 P4b enrichment 完成；P3 不再保留此待辦。

## P4: Candidate Engine v1

目前狀態：raw JSONL candidate builder MVP 與 P4b enrichment MVP 已完成。

- universe 過濾。
- broad pool first。
- archetype 分類。
- 多維排序。
- `next_day_actionable` 初版 label。
- FinMind price raw cache 轉候選：已完成。
- daily report source summary / data gaps：已完成。
- P4b 是 P4 的資料強化，不是獨立大 phase：20-50 日窗口、`TaiwanStockInfo`、法人、融資融券 MVP 已完成。

## P5: Replay / Paper Ledger

目前狀態：已完成 MVP。

- historical replay。
- paper ledger：P0 骨架已完成。
- 成本、滑價、R 值計算：已完成 MVP，以 `cost_r` 假設表示。
- 同一 setup 不重複開倉：ledger 已有；replay 另會 skip duplicate idempotency。
- replay expectancy 只使用 `validity = valid`：已完成。
- gross / cost / net R 分開報表：已完成。

## P6: Shioaji Simulation

目前狀態：已完成。

- P6A 本地 simulation execution chain：已完成 `SignalIntent -> RiskDecision -> OrderIntent -> BrokerTrade -> LedgerPosition` dry-run chain，simulation 與 replay expectancy 分開，duplicate intent 會在 broker order 前被 ledger 擋下。
- P6B callback / restart-sync hardening：已完成 execution sync store、restart-sync、callback normalization / stream、lifecycle policy、duplicate dedupe、status ordering、store locking、terminal-state policy、longer callback smoke。
- P6C gated Shioaji simulation login + callback registration：已完成真實 `sj.Shioaji(simulation=True)` login smoke，`set_order_callback` 可註冊，且所有 gateway 都拒絕 `api.simulation=False`。
- P6D gated simulation order + cancel smoke：已完成真實 simulation order / callback / cancel smoke。最終 smoke summary total 4、ok 4、blocked 0；order status `submitted`，cancel callback `cancelled`，restart-sync matched 2、needs_review 0。
- 安全邊界：正式區登入、正式委託與自動下單仍不屬於本階段；P6 只證明 Shioaji simulation 執行鏈路可控，不證明策略 edge。

## P7: Production Readiness Gate

目前狀態：已完成。

- 新增 `simulate production-readiness`，從 execution sync store 產生 production readiness JSON / Markdown report。
- readiness checks 包含正式 live gate、regular-session policy、pending order limit、partial fill policy、callback ordering、cancel retry plan。
- 正式 live gate 預設 blocked；只有 `--allow-live-trading` 搭配正確 `--manual-approval-token` 才會讓 readiness report 進入 `ready`，且此命令本身不送單。
- pending submitted order、partial fill、callback ordering issue、cancel retry 缺口都會轉成 alerts 與 manual actions。
- P7 smoke 已用 P6 真實 simulation store 驗證：checks 6、ok 5、blocked 1、needs_review 0；唯一 blocker 是未提供正式人工 approval token。
- 安全邊界：P7 只完成正式營運前的 gate/report/alert contract，不啟用正式下單。
- residual risk：P7 readiness 目前是 report，不是 broker boundary enforcement；P11 必須在 live adapter 再實作硬阻擋。
- residual risk：P7 預設 expected token 是可預測字串，只能用於測試；正式 approval token 必須改成不可預測、短效、可稽核的人工授權。

## Later: Report Loop / Notification

目前狀態：hardening 已完成。

- 每日日報：已完成 candidate daily report。
- 單日 close report：已完成，可聚合 candidate / replay / simulation output。
- Telegram 摘要 dry-run：已完成專用 summary renderer，仍禁止真實發送。
- 前一日候選驗證。
- valid / excluded / needs_review 樣本統計：已透過 replay 與 close report 顯示。
- simulation status summary：已完成，`needs_review` 會明確列出，且 simulation 不納入 replay expectancy。
- hardening：逐筆 `needs_review` reason、專用 Telegram summary renderer、壞檔 / 缺欄位測試已完成第一版。
- 下一步：進 P8-P11，不要把 simulation go-live、reports、regression correction 與正式下單設計混成一個 phase。

## P8: Simulation Ops Go-live

目前狀態：已完成。

目標：把 P6/P7 的 gated simulation order chain 變成每日可執行的 simulation ops，不啟用正式區。

- 建立 daily simulation runner：candidate input -> risk decision -> Shioaji simulation order -> callback stream -> restart-sync -> readiness report。
- 支援 dry-run / simulation-on 的雙 gate，預設不送 simulation order。
- 產生單日 execution bundle，包含 input plan、order report、callback store、restart-sync、production-readiness。
- 設定 trading-day / regular-session gate 與盤後禁止策略。
- must-fix：定義 plan builder contract，明確記錄 candidate source、risk decision reason、limit price source、quantity source、blocked reason。
- must-fix：送 simulation order 前檢查 limit_up / limit_down、reference price、contract loaded、regular session、trading day。
- must-fix：建立 `ops_run_manifest`，保存 run id、artifact path、input/output checksum、side effects summary。
- must-fix：P8 完成條件不是「能送 simulation order」，而是 daily run 可從 manifest replay / audit / explain。
- 驗收：可在一個交易日完成 end-to-end simulation run，且所有 side effects、blocked reasons、broker order ids 都可追蹤。

## P9: Ops Reports / Alerts

目前狀態：已完成。

目標：讓每日 simulation ops 結果能被快速看懂與追蹤。

- 實作 `generate_alerts_from_run` 並輸出包含 severity, category, owner, manual_action, send_gate 屬性的 `alerts.json`。
- 實作每日 Close Report 整合 candidate, replay, simulation, restart-sync, readiness checks。
- 實作 Telegram gated 發送功能，支援根據環境變數開關進行 dry-run 或真實發送。

## P10: Regression Correction Loop

目前狀態：已完成。

目標：把每日 simulation ops 的錯誤、mismatch 與 missed cases 轉成可回歸修正的樣本。

- 實作 `RegressionCase` 資料結構，支援 source run id, failure type, minimal fixture path, expected behavior, status, closing test command 等。
- 實作 `regression-import` 及 `regression-run` CLI 指令以實現 TDD 自動化測試回歸閉環。

## P11: Live Execution Design & Broker Adapter

目前狀態：已完成。

目標：在 simulation ops 穩定後，才設計正式區下單。

- 實作 `LiveShioajiBrokerAdapter` 與其嚴格門禁驗證（允許交易開關、熵值足夠的 approval token 驗證及 regression loop 中有無 open items 等）。
- 實作 panic cancel 及全域 kill switch 等安全措施。

---

## Phase B: Resolving Six Core Problems

目前狀態：已完成。

目標：回頭修正舊有專案在策略 Edge、成本估算、風控架構、回測方法、市場微結構及績效回饋等六個層面的致命缺陷。

- **B1: Realistic Cost & Slippage Model**
  - 實作 `TaiwanDayTradeCostModel`，支援 `0.15%` 當沖稅率、`0.1425%` 券商手續費、手續費折讓折扣與台股階梯式 Tick Size 滑價估算。
  - 將其整合至 `ReplayAssumptions` 與 `replay_one_sample`，實現 R 單位動態來回成本估算。
- **B2: Risk Management & Exit Engine**
  - 實作 **Fixed Fractional** 部位規模管理（Position Sizing），動態依帳戶權益、單筆風險比率與停損距離計算下單股數，並自動進行千股無條件捨去與權益上限檢查。
  - 實作 intraday exit 檢查功能（Stop Loss, Take Profit, Time Stop 13:20）。
  - 實作帳戶與部位層級限制（最大持倉上限 3 檔，總曝險 6% 限制）。
- **B3: Market Microstructure Gates**
  - 檢查跌停賣出與漲停買入微結構硬限制，並予以阻擋。
  - 檢查融券做空限制，無券標的禁止 Sell 訊號。
  - 阻擋 13:25-13:30 收盤集合競價時段下單。
- **B4: Backtest Methodology Upgrades**
  - 強制執行 `candidate_date < trading_date`，杜絕前視偏差。
  - 於 replay 中計算 95% 信賴區間，並對樣本數過低（N < 30 或 N < 100）提出統計學警告。
  - 實作 `walk-forward` 滾動驗證 CLI 工具。
- **B5: Performance Feedback Loop**
  - 實作 `RollingPerformanceTracker`，監控滾動 Expectancy 與 drawdown。
  - 當 Expectancy 轉負或 Drawdown 超限時，自動調降 Quantity 至 50% 或完全禁用策略（0%）。
- **B6: Strategy Edge Redesign**
  - 實作 `VwapBreakoutStrategy` 行情訊號產生器。
  - 實作 `atr_20d_pct` 波動度特徵與低 ATR 個股過濾機制。

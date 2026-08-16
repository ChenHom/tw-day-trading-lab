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

---

## Phase C: Trading Day Autonomous Cycle

目前狀態：下一個主線。

目標：把候選股名單、進出場策略、風控、執行鏈、收盤報告與隔日候選名單整理成每個交易日自動重複的作業循環。

這個 phase 修正 `Daily Simulation Ops Automation v1` 之後的方向：`daily-ops` 是 bundle runner / audit layer，不是完整交易日自動交易系統。完整交易日系統必須理解時間、stage、候選監控、部位生命週期、報告發布與隔日 handoff。

### C1: Trading Day Scheduler / Run State

目前狀態：第 1-8 步已完成 fixture / dry-run 版。

- 建立 trading-day state machine。
- 支援 09:05 或 10:00 啟動 policy。
- 固定 13:20 停止新進場與 force-exit / cancel policy。
- 固定 15:00 產生並發布當日 report。
- 固定 17:30 產生下一交易日候選名單。
- 產出 `trading_day_run_state.json`，記錄 stage transition、run id、artifact、locks、retry 與 blocked reasons。
- 交易日不做額外 holiday calendar 判斷；只要 API / raw cache 取不到當日交易資料，就判定為 `non_trading_day`。

驗收：

- fixture trading day 可 dry-run 完整 stage transition：已完成。
- 無交易資料時可輸出 `calendar_status=non_trading_day` 並停在 `blocked`：已完成。
- 重跑同一 trading day 不會重複送單、重複發 report 或覆蓋不可覆蓋的候選清單：dry-run state 已有 idempotency key / lock policy，duplicate intent 會被 `duplicate_suppressed`，真實 side effect enforcement 留到 explicit send / broker gates。

### C2: Intraday Candidate Watch Loop

目前狀態：fixture / raw-cache adapter / dry-run MVP 已完成。

- 只載入既有候選股名單，不盤中掃全市場。
- 透過 market-data port 取得候選股 intraday bars / ticks；目前可由 `--intraday-bars-input` fixture 或 candidate-scoped raw cache adapter 讀取。
- 在 09:05/10:00-13:20 期間反覆檢查 entry strategy。
- 對 open positions 反覆檢查 exit strategy。
- 每次 no-action / rejected / approved signal 都要有 reason 與 artifact。
- 目前輸出 `watch_events.json`，entry 使用 `VwapBreakoutStrategy`，open position 會優先走 exit dry-run，13:20 後新 entry 會被拒絕為 `after_hard_stop`。

驗收：

- fixture / raw-cache bars 可產生 approved entry、rejected entry、exit trigger、no-action candidate：已完成。
- 13:20 後不允許新進場：已完成。

### C3: Execution Policy + Force Exit / Cancel Enforcement

目前狀態：fixture / dry-run MVP 已完成。

- 將 `watch_events.json` 的 approved entry / approved exit 轉成 dry-run order intents。
- 接 fake / Shioaji simulation gate，但預設仍不登入、不送單。
- 處理 duplicate intent、pending order、partial fill、callback ordering、max position、daily risk。
- 13:20 後禁止新進場，並對 open position / stale pending order 產生 force-exit / cancel intent。
- 正式 live trading 繼續 blocked。

驗收：

- force-exit fixture 可證明 open position 被處理或明確列入 manual action：已完成。
- stale pending order 會產生 dry-run cancel intent：已完成。
- duplicate intent 會被 suppress，max positions / daily risk 可 block 新 entry：已完成。
- partial fill lifecycle decision 會列入 manual action：已完成。

### C4: 15:00 GitHub Report Publish

目前狀態：local / would-send dry-run MVP 已完成。

- 產生當日交易結果報告：候選監控、進場、出場、跳過原因、P/L 或 simulation P/L、優點、缺點、blockers、next actions。
- 將報告發布到 GitHub artifact。
- 將 GitHub link 傳給 operator。
- Telegram / external send 仍需 gate 與 dedupe。

驗收：

- dry-run mode 可建立 report artifact 與 would-send link：已完成，`publish_status=dry_run` / `send_status=dry_run_not_sent`。
- 真實發送前必須另有 gate。

### C5: 17:30 Next-Day Candidates

目前狀態：next-candidate handoff dry-run MVP 已完成。

- 盤後 ingestion / validation。
- 產生下一交易日 candidates。
- 保存 candidate run 與 summary。
- 將 candidate artifact 掛到下一個 trading-day run state。

驗收：

- fixture post-market run 可產生 next-day candidate file，並正確關聯下一交易日：已完成。因老闆要求不查額外交易日曆，handoff 只標記 `next_api_available_trading_day`，不猜日期。

### C6: End-to-End Simulate Smoke

目前狀態：fixture / multi-day stability smoke 已完成。

- 一次 dry-run 產出 run state、watch events、order intents、position state、report、next candidates 與 smoke summary。
- smoke 檢查 required artifacts 是否存在。
- 所有 artifact 仍保持 `side_effects=[]`，不登入券商、不送單、不發通知。
- `simulate trading-day-cycle-smoke` 可連跑多個日期，輸出 `multi_day_smoke_summary.json` / `multi_day_smoke.md`；有 API/raw-cache trading rows 的日期才檢查完整 artifact chain，取不到資料的日期標為 `skipped_non_trading_day`。

驗收：

- 單日完整 artifact chain `end_to_end_smoke.status=ok`：已完成。
- 多日 smoke 可區分 ok trading day 與 non-trading day skip：已完成。

### D1: Simulate Side-Effect Gate Preparation

目前狀態：未完成。

- 將目前 dry-run order / cancel intents 接到 Shioaji simulation side-effect gate。
- 沒有 explicit gate 時，不得登入 Shioaji、不送單、不取消。
- gate 開啟時仍只允許 `simulation=True`，正式 live trading 繼續 blocked。

驗收：

- 未開 gate 時，multi-day smoke 仍只產生 artifact，不產生 broker side effect。
- 開 gate 的 simulation smoke 必須留下 login / order / cancel evidence，且不可保存 secret。

### D2: Report Publish / Operator Send Gate

目前狀態：未完成。

- 將 15:00 local report dry-run 接到 GitHub publish gate。
- 將 operator link send 接到 Telegram send gate。
- 必須有 dedupe key，避免重跑同一 trading day 重複發送。

驗收：

- 未開 gate 時維持 `publish_status=dry_run` / `send_status=dry_run_not_sent`。
- 開 gate 後才允許真實 GitHub artifact / Telegram operator link。

## Phase E: Price Action Intraday

### 編號衝突警告

本文件的 P0-P11 與 `docs/price-action-intraday-plan.md` 的 P1-P9 是**兩套不同編號**。

| 編號 | 本 roadmap | Price Action plan |
|---|---|---|
| P1 | Old Log / CSV Importer | Shioaji Tick → MarketTick |
| P2 | TiDB Integration | Tick → 1m Aggregator |
| P3 | FinMind Nightly Ingestion | 1m → 5m Aggregator |

以下一律用 `PA-Pn` 前綴指涉 Price Action 的階段。

### 兩段驗收模型

Price Action 各階段都拆成兩個 gate。這樣非交易時段不會卡住開發，也不會把 unit test 全綠誤讀成真實行情已驗證。

```text
PA-Pn_CODE_COMPLETE     fixture / synthetic 測試全部通過
PA-Pn_LIVE_VALIDATED    真實行情跑過且資料無 loss
```

`LIVE_VALIDATED` 未達成前，不得宣告該段 pipeline 已可用於每日 paper trading。

### PA-P1: Shioaji Tick → normalized MarketTick

目前狀態：`PA-P1_CODE_COMPLETE` 已完成，`PA-P1_LIVE_VALIDATED` 未完成。

- provider adapter 與 normalization 都在 `market_data.py`，該模組不 import Shioaji SDK。
- provider callback 只入佇列，raw 寫入 / normalize / dedupe / sink 在 worker thread。
- `simtrade` / 盤中零股 / 暫停交易 / 壞 volume 一律不進下游；成交量統一換算成股。
- candidate scope 在 subscribe 端與 ingestion 端各檢查一次。
- market data health 聚合成 `HEALTHY` / `DEGRADED` / `FAILED`；非 HEALTHY 時 smoke fail closed 並回傳非 0。
- `simulate shioaji-tick-smoke` 預設 blocked，不 import SDK、不登入、不訂閱。

驗收：

- Gate A：21 項 checklist 全數完成，unit / adversarial tests 通過。已完成，見 `docs/price-action-p1-design.md`。
- Gate B：交易時段 smoke 的 report 需 `live_validation.passed=true`。未完成。

### PA-P2: MarketTick → canonical 1m MarketBar

目前狀態：`PA-P2_CODE_COMPLETE` 已完成，`PA-P2_LIVE_VALIDATED` 未完成。

- 半開 bucket `[09:00:00, 09:01:00)`，事件時間 watermark，多股票各自狀態。
- late tick：窗內併入、窗外 `CORRECTED` rev+1、超窗 `dropped_late`。
- **沒有成交的分鐘不補 synthetic bar**，以保持「真的量 0 / 沒人交易 / feed 漏資料」三者可分辨。此決定推翻 `docs/price-action-intraday-plan.md` 的原始 Missing Minute Policy。
- `bars build` 可從 PA-P1 raw tick 重建 1m bar；replay 與 live 共用同一套 aggregator。

驗收：

- Gate A：OHLCV / bucket / rollover / multi-symbol / late / missing / replay determinism 測試通過。已完成。
- Gate B：真實 tick 聚合出的 1m 與盤後 provider 1m 比對 OHLC / volume / bar count / missing minute。未完成，且卡在 FinMind 分 K 單位未查證。

### PA-P3: canonical 1m → canonical 5m

目前狀態：`PA-P3_CODE_COMPLETE` 已完成，`PA-P3_LIVE_VALIDATED` 未完成。

- 半開 bucket `[09:00, 09:05)`；5m 永遠不向 provider 取得，只接受 `timeframe="1m"` 輸入。
- bucket 是時間區間不是「五根 1m」；缺分鐘照樣產生 5m，不補假資料、不借下一個 bucket 湊數。
- 1m correction 會 replace 該分鐘並重算整根 5m，不做增量累加。

驗收：

- Gate A：bucket boundary / OHLCV / 缺分鐘 / correction / deterministic replay 五項測試通過。已完成。
- Gate B：與盤後 provider 5m 比對。未完成。

### PA-P4: Bar Persistence / Replay

目前狀態：`PA-P4_CODE_COMPLETE` 已完成。無 Gate B。

- append-only JSONL event store：`data/bars/{timeframe}/{date}/{symbol}.jsonl`。
- timeframe 是目錄層級，1m 與 5m 結構上不可能 key collision。
- correction 是新增一行而非覆寫；策略當時看到的 revision 永遠可取回。
- `bars build --store-dir` 會把完整 emission log（含 correction）寫入 store。

驗收：8 項 checklist 全數完成。核心是「跑一場 session → 只從磁碟讀回 → 與記憶體結果完全相同」。詳見 `docs/price-action-p4-design.md`。

### PA-P5: TOD-RVOL / Cumulative RVOL

目前狀態：`PA-P5_CODE_COMPLETE` 已完成，`PA-P5_LIVE_VALIDATED` 未完成。

- baseline 按相同 time slot 比較，取 median（mean 保留供對照），窗口 20 個交易日。
- 缺 bar 的日子不貢獻樣本，**不當成 0**；否則 baseline 會被拉低並製造假的量能突破。
- 樣本不足時回 `insufficient_data` 且**不回傳比值**，避免有人拿三天歷史算出的數字去交易。
- baseline 來源改用 Shioaji `api.kbars` 盤前 backfill（`bars backfill-kbars`，預設 blocked）；FinMind 分 K 需付費 sponsor 等級，free tier 直接 400。

驗收：

- Gate A：7 項 checklist 全數完成。已完成。
- Gate B：真實 backfill 跑過、kbar volume 單位以日 K 定案、每日 bar 數合理、20 日 baseline 建立完成。未完成。

### PA-P6 / PA-P7 / PA-P8

目前狀態：三者皆 `CODE_COMPLETE`，共用一個未達成的 Gate B。

- PA-P6 `structure.py`：swing 需兩側各 N 根確認，最新 N 根不算 swing；嚴格不等，不依賴 tie-break。HH/HL/LH/LL 與 BOS 規則明確，swing 不足回 `UNKNOWN` 而非猜測。
- PA-P7 `setup.py`：Breakout → Retest → Trigger 狀態機。RVOL 缺失或 `insufficient_data` 一律不 breakout；同一 breakout level 每個 symbol 只用一次；entry / stop / target 在 signal 當下全部確定。
- PA-P8 `paper.py`：同一根 bar 內先判 stop 再判 target；`setup_id` 永不釋放；market data 不健康禁止新進場；收盤前一定清倉。被擋下的訊號保存理由。

驗收：三份 checklist 全數完成（共 39 tests）。詳見 `docs/price-action-p6-p8-design.md`。

**Gate B 未達成前，paper trade 結果只證明 determinism 與規則遵循，不得解讀成 strategy edge。**

### PA-P9

未開始：daily performance report、expectancy report、ablation comparison（Breakout vs +RVOL vs +Retest vs +Trigger），並把既有 `cost.py` 的成本 / 滑價接進 paper trade 的 R 計算。

判定 feature 是否真的增加 edge 時使用 net expectancy / profit factor / win rate / MFE / MAE / trade count / drawdown，不能因為回測變漂亮就保留。

### 整條 PA 線的共同阻斷

所有 `LIVE_VALIDATED` 都卡在同一件事：一次交易時段的真實行情驗證。

1. `simulate shioaji-tick-smoke` → 關 PA-P1/P2/P3 Gate B。
2. `bars backfill-kbars` + `check_backfill_against_daily` 定案 kbar volume 單位 → 關 PA-P5 Gate B。
3. 上述通過後，PA-P6/P7/P8 的 Gate B 才有意義。

在那之前 paper trade 結果只證明 determinism 與規則遵循，不是 strategy edge。

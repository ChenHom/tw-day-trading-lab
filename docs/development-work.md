# Development Work

日期：2026-05-28  
狀態：P0-P11 已完成；Phase B 已完成；Post-market hardening Sprint 3-A/B/C 與 Sprint 4-A/B/C 已完成。最新下一步是 `Daily Simulation Ops Automation v1`：把 ingestion -> candidate build -> ops-run -> report / alerts -> regression-import 串成每日可重跑作業鏈，不是回頭重做 P8，也不是推正式下單。
預設分支：`master`

## Phase 對照

本文件採用 P0-P11 作為目前開發 phase 命名。`docs/mvp-roadmap.md` 必須與本文件保持一致。

| Phase | 名稱 | 目前狀態 | 對應 commit |
|---|---|---|---|
| P0 | Bootstrap 固化 | 已完成 | `4aab4e8` |
| P1 | Old Log / CSV Importer | 已完成 MVP | `579efec` |
| P2 | TiDB Integration | 已完成 MVP | `f614228` |
| P3 | FinMind Nightly Ingestion | 已完成 cache / ledger MVP | `49a917b` |
| P4 | Candidate Engine v1 | 已完成 raw cache candidate builder MVP | `fbdff38` |
| P4b | Candidate Engine Data Enrichment | 已完成 MVP | `2b56adf` |
| P5 | Replay / Paper Ledger | 已完成 MVP | `3c998be` |
| P6 | Shioaji Simulation Adapter | 已完成 | through P6D smoke |
| P7 | Production Readiness Gate | 已完成 | P7 readiness gate |
| P8 | Simulation Ops Go-live | 已完成 | P8 daily ops manifest |
| P9 | Ops Reports / Alerts | 待處理 | - |
| P10 | Regression Correction Loop | 待處理 | - |
| P11 | Live Execution Design | 待處理 | - |

`P4b Candidate Engine Data Enrichment` 是 P4 的資料強化 sprint，不是獨立大 phase。

## Phase Close-out Rule

每個 phase / sprint 完成後，必須固定執行 grill-me review，再收尾。這不是可選項。

流程：

1. 整理本 phase 實作範圍、測試結果、文件更新與 commit。
2. 用 grill-me review 檢查：
   - 實作是否偏離原始目標。
   - 文件是否和實際行為一致。
   - 驗證是否只覆蓋 happy path。
   - 是否有把 simulation / replay / live execution 混在一起。
   - 下一步是否應先 hardening，而不是直接擴張功能。
3. 將 grill-me 結論寫回本文件對應 phase：
   - verdict / 方向判斷。
   - must-fix 風險。
   - should-fix 優化。
   - next sprint 建議。
4. 必要時同步 `docs/mvp-roadmap.md`、README 與 memory。

目的：避免 phase 看起來完成，但實際上只完成「產出」，沒有完成「可營運、可排錯、可接手」。

## 0. Grill-me 結論

目前沒有更多阻塞型問題需要先問。已決定：

- 新 repo：`~/services/stock/tw-day-trading-lab`
- 預設分支：`master`
- 最高原則：先產生價值，再擴張架構
- 第一個真實資料工作：舊 log / CSV importer
- 第二個真實資料工作：FinMind nightly ingestion
- 第一階段禁止真實自動下單；目前所有下單相關實作只允許使用 Shioaji simulation / fake SDK 完成開發與驗證
- Shioaji simulation 只驗證執行鏈路，不單獨證明策略 edge

舊 log importer 是反例測試器，不是舊策略復活。它要先確認新系統能正確處理舊失敗：debug 樣本、盤後測試、重複開倉、缺 exit、資料污染與不可信 expectancy。

## 1. 成功定義

第一階段成功不是「能下單」，而是每天能可靠回答：

1. 明天哪些標的值得看？
2. 昨天候選是否真的形成可交易機會？
3. 舊失敗樣本是否被正確分級為 `valid`、`excluded`、`needs_review`？
4. 新 ledger 是否能防止同 setup 重複開倉？
5. replay / simulation 樣本是否分開統計，沒有互相污染？

## 2. Non-negotiables

- 不接正式自動下單。
- 不用 raw signal log 直接計算績效。
- 不把 simulation 成交結果當成策略 edge 證明。
- 所有策略 expectancy 只吃 `validity = valid` 的樣本。
- 所有 order intent 必須有 `idempotency_key`。
- 報告必須同時列出 valid / excluded / needs_review 數量。
- API quota 必須可追蹤，FinMind planned calls 不超過 540/hr。

## 3. 開發順序

### P0. Bootstrap 固化

目前狀態：已完成。

已完成：

- repo 建立於 `~/services/stock/tw-day-trading-lab`
- branch 改為 `master`
- README / docs / TiDB schema / CLI skeleton
- candidate engine
- paper ledger
- Markdown / HTML report
- sample fixture
- unittest

驗收：

- `PYTHONPATH=src python3 -m unittest discover -s tests` 通過
- sample candidate build 通過
- sample report 通過
- git status clean

### P1. Old Log / CSV Importer

目前狀態：已完成 MVP。

目標：把舊專案失敗資料轉成新系統可驗證樣本。

工作項目：

- 掃描舊 repo log / CSV 路徑。
- 定義 importer input contract。
- 匯入舊交易事件：entry、exit、symbol、timestamp、position id、reason、R 值欄位。
- 建立 `TradeLifecycle` normalized model。
- 偵測同秒同標的雙 `ENTER`。
- 偵測缺 exit、盤後測試、debug / forced order、跨日污染。
- 輸出 `valid_samples` compatible JSON。
- 產生 failure replay report。

驗收：

- 舊 log 中已知雙 `ENTER` 樣本被標記為 `excluded` 或 `needs_review`。
- debug / forced / after-hours 樣本不得進入 expectancy。
- importer 對缺欄位不 crash，改輸出 `needs_review`。
- report 列出 valid / excluded / needs_review 數量與主要排除原因。
- tests 覆蓋至少：
  - normal complete lifecycle
  - duplicate enter
  - missing exit
  - after-hours/debug sample
  - malformed row

建議產出：

- `src/tw_day_trading_lab/old_log_importer.py`
- `tests/test_old_log_importer.py`
- `docs/old-log-importer.md`
- `examples/old-log.sample.csv`

實際產出：

- `src/tw_day_trading_lab/old_log_importer.py`
- `tests/test_old_log_importer.py`
- `docs/old-log-importer.md`
- `examples/old-log.sample.csv`
- CLI：`tw-daytrade old-logs import`
- Daily report sample summary：`tw-daytrade report daily --samples ...`

驗證結果：

- sample fixture：valid / excluded / needs_review = 1 / 3 / 1。
- 舊 log `2026-03-25`：events 1410，valid / excluded / needs_review = 0 / 8 / 0，8 筆皆因 `duplicate_enter_same_symbol_timestamp` 排除。
- 舊 log `2026-04-01`：events 2467，valid / excluded / needs_review = 0 / 8 / 0，2 筆雙 ENTER、6 筆 after-hours entry 排除。

### P2. TiDB Integration

目前狀態：已完成 MVP。

目標：讓 metadata / sample / quota ledger 可保存與查詢。

工作項目：

- 建立 DB adapter，先用標準 `sqlite3` 或 optional TiDB driver wrapper 抽象介面；核心邏輯不可依賴 DB client。
- 將 `sql/001_init.sql` 實際套用到 TiDB。
- 寫入 candidate runs、candidate items、valid samples、order intents。
- 建立 migration smoke test 文件。

驗收：

- TiDB schema 可重複執行。
- sample valid/excluded/needs_review 可寫入與讀回。
- DB 失敗不讓策略核心吞錯；要明確回報。

實際產出：

- `src/tw_day_trading_lab/storage.py`
- `tests/test_storage.py`
- `docs/tidb-integration.md`
- CLI：`tw-daytrade db init`
- CLI：`tw-daytrade samples persist`
- CLI：`tw-daytrade samples summary`
- CLI：`tw-daytrade candidates persist`

設計結果：

- `SampleRepository` / `CandidateRepository` protocol 是核心可依賴的 port。
- `DatabaseStorage` 是 DB-API adapter；unit tests 用 SQLite，runtime 用 TiDB。
- `connect_tidb` 只在 adapter / CLI 邊界使用，importer / classifier 不依賴 DB client。
- `valid_samples.idempotency_key` 改為非唯一 index，以保留 duplicate ENTER 反例；真正的防重複下單仍由 `order_intents.idempotency_key` primary key 負責。

驗證結果：

- `PYTHONPATH=src python3 -m unittest discover -s tests -v`：15 tests OK。
- `PYTHONPATH=src python3 -m compileall -q src tests`：OK。
- `tw-daytrade db init` 連續執行兩次均成功。
- TiDB sample persist / summary：valid / excluded / needs_review = 1 / 3 / 1。
- TiDB duplicate evidence：同一 idempotency key 保留 2 rows。
- TiDB candidate persist：`p2-smoke-2026-05-28` item_count 3，actionable_count 2。

### P3. FinMind Nightly Ingestion

目前狀態：已完成 cache / ledger MVP。

目標：建立隔日候選名單的夜間資料來源。

工作項目：

- `fetch_ledger` 記錄 dataset/date/stock/source/status/request_count。
- 本地快取優先，抓過不重抓。
- planned calls 以 540/hr 為上限。
- 先抓高流動 universe，再對 shortlist 補籌碼資料。
- 匯出 raw Parquet 或暫時 JSONL，後續再接 Parquet library。

驗收：

- 無 token 時不 crash，回報 setup / auth 缺口。
- 有 token 時能查 `user_info` 或等價 quota 狀態。
- 同一 dataset/date/stock 重跑不重複打 API。
- ingestion summary 顯示 planned / actual / skipped / failed calls。

實際產出：

- `src/tw_day_trading_lab/finmind_ingestion.py`
- `tests/test_finmind_ingestion.py`
- `docs/finmind-ingestion.md`
- `examples/finmind.requests.sample.json`
- CLI：`tw-daytrade ingest finmind`
- `FetchLedgerRepository` port
- `DatabaseStorage.fetch_fetch_record`
- `DatabaseStorage.save_fetch_record`

設計結果：

- P3 API cache 拆成 control layer 與 raw data layer。
- control layer 使用 TiDB `fetch_ledger` 判斷同一 `dataset / trading_date / stock_id / source` 是否已成功。
- raw data layer 先用 JSONL：`data/raw/finmind/{dataset}/{date}/{stock_id}.jsonl`。
- skip 必須同時滿足 ledger success 與 raw cache 存在。
- ledger success 但 raw cache 遺失時會重新抓取，summary 標記 `refetched_missing_cache`。
- FinMind SDK adapter 只在 composition boundary；核心 ingestion 依賴 `FinMindClient` protocol 與 `FetchLedgerRepository` protocol。

驗證結果：

- `tests/test_finmind_ingestion.py` 覆蓋無 token、cache skip、missing cache refetch、quota block、client error ledger。
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`：20 tests OK。
- 真實 FinMind smoke：第一次 planned / actual / skipped / failed = 1 / 1 / 0 / 0；第二次同 request = 0 / 0 / 1 / 0。
- key 來源確認：舊專案 `quantitative-trading-decision-system/.env` 只有 placeholder；實際可用 key 來自 `/home/hom/services/stocks-db/.env` 的 `FINMIND_API_KEY`，未寫入本 repo。

### P4. Candidate Engine v1

目前狀態：已完成 raw cache candidate builder MVP。

目標：把夜間資料轉成隔日監控排序。

工作項目：

- universe filter。
- broad pool first。
- archetype 分類。
- `next_day_actionable` 初版 label。
- 降權原因。
- Top 30-80 report。

驗收：

- 候選名單不是買進名單。
- 每檔候選都有 score、archetype、reasons、downgrade_reasons。
- 報告可追溯資料缺口與 API 用量。

實際產出：

- `src/tw_day_trading_lab/candidate_builder.py`
- `tests/test_candidate_builder.py`
- `docs/candidate-engine-v1.md`
- CLI：`tw-daytrade candidates build-from-raw`
- Daily report source summary：資料來源、input files、built candidates、degraded candidates、data gap files、低流動性過濾數。

設計結果：

- P4 先從 `TaiwanStockPrice` raw JSONL 建立最小可行候選，不直接接買賣訊號。
- `build_candidates_from_raw_cache` 是 orchestration；讀 raw cache、做 liquidity filter、轉 `CandidateInput`、呼叫既有 `rank_candidates`。
- 單日資料不足時標 `data_quality = degraded`，讓候選保留可追溯性但不會被標成 `next_day_actionable`。
- malformed raw file 不 crash，summary 記入 `data_gap_files`。

驗證結果：

- `tests/test_candidate_builder.py` 覆蓋 raw cache 建候選、低流動性過濾、degraded data、malformed data gap。
- `tests/test_reports.py` 補 daily report source summary 測試。
- 真實 FinMind P3 smoke 取得 `2330` 2026-05-28 raw cache 後，`candidates build-from-raw` 可產出 1 筆 degraded candidate；日報會列出資料來源 summary。

### P4b. Candidate Engine Data Enrichment

目前狀態：已完成 MVP。

定位：P4 的資料強化，不是新的大 phase。

目標：讓 candidate engine 不只吃單日 price raw cache，而是能用足夠歷史窗口與基本 universe / chip 資料產生更可靠的隔日候選。

工作項目：

- P3 ingestion 改抓 20-50 日窗口，而不是單日，讓 volume expansion / structure 更可靠：已完成 `start_date`。
- 接 `TaiwanStockInfo` 做 ETF / 權證 / 特別股排除：已完成 MVP。
- 接法人與融資融券資料，缺資料時降權而不是 crash：已完成 MVP。
- 將 cache summary 與 fetch ledger summary 更完整帶入 report：已完成 candidate source summary；fetch ledger summary 後續再做 aggregate。
- 建立 Top 30-80 fixture 與 regression report：尚未完成，留到後續 broad universe sprint。

驗收：

- 候選名單仍不是買進名單。
- 每檔候選都有 score、archetype、reasons、downgrade_reasons。
- 報告可追溯資料缺口與 API/cache 用量。
- 缺 chip / margin 資料時標記 degraded，不中斷流程。
- tests 通過。

實際產出：

- `FetchRequest.start_date`
- `cache_satisfies_request`
- `tw-daytrade ingest finmind --start-date ...`
- `examples/finmind.requests.sample.json` 更新為 price / stock info / institutional / margin 四筆 request。
- `candidate_builder` 讀取 `TaiwanStockInfo`、`TaiwanStockInstitutionalInvestorsBuySell`、`TaiwanStockMarginPurchaseShortSale`。
- daily report source summary 新增非普通股過濾、缺法人資料、缺融資融券資料。

驗證結果：

- `tests/test_finmind_ingestion.py` 補歷史窗口與舊 cache 太短時重抓測試。
- `tests/test_candidate_builder.py` 補 ETF 排除、法人/融資融券 enrichment、缺 enrichment 降權測試。
- 真實 FinMind smoke：四筆 request 第一次 planned / actual / skipped / failed = 4 / 4 / 0 / 0；第二次 = 0 / 0 / 4 / 0。
- 真實 raw cache candidate：`2330` / 台積電，degraded candidates 0，缺法人 / 缺融資融券皆為 0。

### P5. Replay / Paper Ledger

目前狀態：已完成 MVP。

目標：用歷史資料與 normalized samples 驗證策略生命週期。

工作項目：

- replay runner。
- paper ledger lifecycle。
- MFE / MAE / realized R。
- cost / slippage assumptions。
- expectancy report。

驗收：

- 同一 `idempotency_key` 不會重複開倉。
- replay expectancy 只使用 `validity = valid`。
- 報表分開列示 gross / cost / net R。

實際產出：

- `src/tw_day_trading_lab/replay.py`
- `tests/test_replay.py`
- CLI：`tw-daytrade replay samples`
- Markdown replay report：`render_replay_markdown`

設計結果：

- `replay_samples` 只把 `validity = valid` 納入 expectancy。
- `excluded` / `needs_review` 樣本只列入 skipped summary，不進策略期望值。
- 同一 `idempotency_key` 的 valid sample 只 replay 第一筆，後續標為 duplicate skip。
- 若 sample 已有 `realized_r_gross`，直接沿用；若沒有，使用 `entry_price / exit_price / stop_price` 計算 gross R。
- 若提供 price bars，使用 high / low 計算 MFE / MAE。
- cost / slippage 先以 `ReplayAssumptions.cost_r` 表示，報表分開列示 gross / cost / net R。

驗證結果：

- `tests/test_replay.py` 覆蓋 valid-only expectancy、MFE / MAE / net R、duplicate idempotency skip、gross / cost / net 報表。
- 舊 sample smoke：`reports/old-log-sample-samples.json` 共 5 samples；replayed 1、skipped excluded 3、skipped needs_review 1；cost_r 0.1 後 net expectancy = -1.3R。

### P6. Shioaji Simulation Adapter

目標：驗證 live-like execution chain。

工作項目：

- `SignalIntent -> RiskDecision -> OrderIntent -> BrokerTrade -> LedgerPosition`
- simulation mode login / place order dry-run wrapper。
- broker callback / status normalization。
- restart sync design。

驗收：

- simulation 樣本與 replay 樣本分開統計。
- 重送同一 intent 不會重複開倉。
- broker state 與 ledger 不一致時標為 `needs_review`，不得自動算入 expectancy。

目前狀態：已完成 dry-run adapter MVP。

實際產出：

- `src/tw_day_trading_lab/simulation.py`
- `tests/test_simulation.py`
- `examples/simulation-plan.sample.json`
- CLI：`tw-daytrade simulate run`

設計結果：

- `ShioajiSimulationAdapter` 依賴 `SimulationBroker` protocol 與 `PaperLedger`，核心不 import Shioaji SDK。
- P6 MVP 使用 `DryRunSimulationBroker` 驗證 simulation login / place order chain。
- `SignalIntent` 轉 `OrderIntent` 後先經 ledger 註冊，duplicate intent 會在 broker order 前被拒絕。
- `BrokerTrade` status 會 normalize，未知 status 進 `needs_review`。
- `reconcile_broker_trades` 會檢查 broker trade 是否存在對應 ledger open intent；不一致時輸出 `validity=needs_review` 且 `expectancy_eligible=false`。
- simulation result 的 `sample_type=simulation`，不與 replay output 混用。

驗證結果：

- `tests/test_simulation.py` 覆蓋 approved signal dry-run、duplicate intent、risk rejected、broker / ledger mismatch、status normalization、CLI output 與 Markdown report。
- sample smoke：`examples/simulation-plan.sample.json` 產出 2 筆 simulation results，其中 1 筆 simulated、1 筆 duplicate，`expectancy_eligible=0`。

## 4. Immediate Next Sprint

目前狀態：Report Loop / Notification hardening 已完成。

任務切分：

1. 將 candidate report、replay report、simulation report 組成單日 close report：已完成。
2. Telegram summary 仍先維持 dry-run gate，不做真實發送：已完成。
3. 報告固定列出 candidate source summary、replay expectancy、simulation status summary：已完成。
4. 將 broker / ledger `needs_review` 顯示在每日報告，不讓它被 expectancy 吞掉：已完成。
5. 補 report loop tests：已完成。
6. commit：已完成，`1612271 Add report loop close summary`。

完成標準：

- 單日報告可同時讀 candidate / replay / simulation outputs。
- 報告中明確分離 strategy expectancy 與 execution-chain simulation。
- `needs_review` 顯示清楚，不能被當成 valid 成果。
- tests 通過。

實際產出：

- `render_close_report_markdown`
- `render_close_report_telegram_summary`
- CLI：`tw-daytrade report close`
- CLI：`tw-daytrade report close --telegram-summary-output ...`
- close report 可列出逐筆 `needs_review` reason。
- close report summary 可接 `notify telegram --dry-run`，真實發送仍 disabled。
- `tests/test_reports.py` report loop regression

驗證結果：

- `tests.test_reports`：6 tests OK。
- full unittest：45 tests OK。
- close report smoke：`reports/2026-05-28-close.md` 可由 candidate / replay / simulation JSON 產生。
- Telegram summary smoke：`reports/2026-05-28-telegram-summary.txt` 可由結構化 summary 產生。
- 壞檔 shape 測試：replay payload 若缺 `summary` 會明確失敗，不產生假正常報告。

Grill-me review：

- hardening 後方向仍正確：report loop 只做聚合、揭露與輸出整形，沒有混入策略判斷。
- 原 must-fix 1 已處理：close report 會列逐筆 `needs_review` reason、symbol、idempotency key。
- 原 must-fix 2 已處理：新增 `render_close_report_telegram_summary`，不再依賴前 12 行截斷作為專用 summary。
- 原 must-fix 3 已部分處理：replay / simulation payload 缺 `summary` 會明確失敗；完整 JSON schema validation 可留到日常排程化前。
- 剩餘優化：daily health score、close report summary persist 到 TiDB、Telegram action hints，可在 execution sync 後依實際痛點補。

Reporting hardening 的主要功能：

- 將報告從「有產出」提升成「可值班、可排錯、可接手」。
- 讓每日 report 不只回答總數，而能指出：
  - 哪些資料可相信。
  - 哪些樣本不可納入 expectancy。
  - 哪些 broker / ledger / simulation 狀態需要人工檢查。
  - Telegram 摘要是否足以讓人快速判斷要不要介入。

如何幫助：

- 減少每天手動打開多份 JSON / Markdown 比對的成本。
- 避免 `needs_review` 只有數量、沒有原因，導致隔天排錯困難。
- 在接 Shioaji callback / restart sync 前，先建立一致的異常揭露格式。
- 讓 future operator 可以先看 Telegram summary，再決定是否打開完整 close report。

未來延伸：

- 短期：完整 JSON schema validation 與更細緻的錯誤碼。
- 中期：產生 daily health score，例如 candidate data completeness、replay confidence、simulation integrity。
- 中期：將 close report summary persist 到 TiDB，追蹤每天資料品質與 execution-chain 健康度。
- 後期：Telegram summary 加 action hints，例如「資料缺法人，候選降權」、「broker / ledger mismatch，暫停下一步模擬」。
- 後期：接 PWA / dashboard，讓 close report 成為 trading ops control panel，而不只是文字輸出。

推薦下一個 sprint：Shioaji callback streaming。

目標：接真正 Shioaji SDK simulation login / callback streaming，並建立 restart sync persistence，用於比對 broker open state 與 ledger state。

### Execution Sync MVP

目前狀態：已完成 MVP。

目標：讓 simulation dry-run 產生可重啟後比對的 execution state，先驗證 broker / ledger 同步 contract，再接真正 Shioaji callback。

實際產出：

- `FileExecutionSyncStore`
- `restore_ledger_from_positions`
- `build_restart_sync_report`
- `order_intent_from_idempotency_key`
- CLI：`tw-daytrade simulate run --execution-sync-store ...`
- CLI：`tw-daytrade simulate restart-sync --store ...`

設計結果：

- execution sync store 保存 `broker_trades`、`open_positions`、`results`。
- restart-sync 會從 persisted open positions 重建 `PaperLedger` open intent keys。
- broker trade 找不到 ledger open intent 時標 `ledger_missing_open_intent`。
- ledger open position 找不到 broker trade 時標 `ledger_missing_broker_trade`。
- 所有 restart sync samples 仍 `expectancy_eligible=false`，不得污染 replay expectancy。
- 目前不自動修正 broker / ledger state；不一致只輸出 `needs_review`。

驗證結果：

- `tests/test_simulation.py` 覆蓋 file store persistence、ledger restore、matched restart sync、ledger-only missing broker trade、CLI run persistence、CLI restart-sync。
- full unittest：51 tests OK。
- compile check：OK。
- diff check：OK。
- smoke：`simulate run --execution-sync-store` 後接 `simulate restart-sync --store`，輸出 checked 1、matched 1、needs_review 0。

Grill-me review：

- 方向正確：這輪只做可重啟狀態保存與比對，沒有接真實下單，也沒有把 execution sync 樣本算入 expectancy。
- must-fix 已處理：restart 後可以重建 ledger open keys，並能同時偵測 broker-only 與 ledger-only mismatch。
- 剩餘風險：目前 store 是 JSON file，沒有鎖與 transaction；若未來有多進程或 callback 並發寫入，必須改成 DB-backed repository 或加 atomic write。
- 剩餘風險：真正 Shioaji SDK callback payload 尚未接入，下一 sprint 要先做 callback payload normalization，再接 live-like event streaming。
- 剩餘風險：restart-sync 目前只比對 open state，不處理部分成交、取消後重送、盤後收斂策略；這些要跟 callback streaming 一起定義。

### Shioaji Callback Normalization MVP

目前狀態：已完成 MVP。

目標：先把 Shioaji order callback `{stat, msg}` normalize 成穩定內部 event contract，避免之後直接把 SDK callback shape 滲進核心邏輯。

實際產出：

- `ExecutionCallbackEvent`
- `normalize_shioaji_order_callback`
- `FileExecutionSyncStore.record_callback_event`
- CLI：`tw-daytrade simulate ingest-callback`
- sample：`examples/shioaji-callback.sample.json`

設計結果：

- callback event 會保存 `stat`、broker order id、`idempotency_key`、symbol、side、quantity、price、normalized status、raw status、review reason 與 raw payload。
- `idempotency_key` 優先從 Shioaji order `custom_field` 取得；缺 key 會標 `missing_idempotency_key`。
- callback event 可轉成 `BrokerTrade` 並寫入 execution sync store，供 restart-sync 比對。
- callback-only event 若沒有對應 ledger open intent，restart-sync 會輸出 `ledger_missing_open_intent`，不會自動修正。

驗證結果：

- `tests/test_simulation.py` 覆蓋 callback dict normalization、missing idempotency key、store callback event、CLI ingest-callback。
- full unittest：55 tests OK。
- compile check：OK。
- diff check：OK。
- smoke：`simulate ingest-callback` 後接 `simulate restart-sync`，callback-only event 正確輸出 `ledger_missing_open_intent`。

Grill-me review：

- 方向正確：先 normalize callback contract，再接 streaming，避免 SDK payload 直接污染核心。
- must-fix 已處理：缺 `idempotency_key` 不會被當成正常 trade，而是 `needs_review`。
- 剩餘風險：目前只支援 dict / object-like payload 的欄位抽取，尚未用真實 Shioaji callback payload 做 live smoke。
- 剩餘風險：`custom_field` 如何在真正 place order 時填入 idempotency key，下一 sprint 必須接上，否則 callback 無法穩定對應 ledger intent。
- 剩餘風險：partial fill / cancelled / rejected 的 position lifecycle policy 尚未定義，只先 normalize status。

### Shioaji Order Custom Field Contract / SDK Gateway MVP

目前狀態：已完成 MVP。

目標：補上 place order 前的 adapter contract，並對齊真實 Shioaji SDK 的 `custom_field` 限制，讓 callback normalization 能穩定回連 ledger intent。

實際產出：

- `ShioajiOrderRequest`
- `build_shioaji_custom_field`
- `build_shioaji_order_request`
- `ShioajiOrderGateway` protocol
- `ShioajiOrderRequestBroker`
- `ShioajiSdkSimulationGateway`

設計結果：

- `build_shioaji_order_request` 是 `OrderIntent -> ShioajiOrderRequest` 的唯一轉換邊界。
- 已確認本機 Shioaji 1.3.2 `Order.custom_field` 最長 6 字元，不能直接放完整 `idempotency_key`。
- request 保留完整 `idempotency_key`，但實際寫入 SDK order 的 `custom_field` 是穩定 6 字元 token。
- execution sync store 會保存 `custom_field -> idempotency_key` mapping，callback normalization 透過 mapping 還原完整 intent。
- 無法還原的短 `custom_field` 會標 `unresolved_custom_field`，不會被當成正常 trade。
- `quantity` / `price` 優先使用 `RiskDecision` 覆寫值，未覆寫才使用 `SignalIntent`。
- `ShioajiOrderRequestBroker` 只依賴 gateway protocol，可用 fake gateway 驗證，不匯入 SDK、不登入真實帳號、不送出真實委託。
- `ShioajiSdkSimulationGateway` 可用 fake SDK 驗證 `login`、`api.Order`、`api.place_order` 邊界；測試不登入真實帳號、不送真實委託。
- `ShioajiSdkSimulationGateway` 會拒絕 `api.simulation=False`，避免開發階段誤接正式區 API。

驗證結果：

- TDD red：新增測試後，因缺 `ShioajiOrderRequest` import 失敗。
- TDD red：新增 SDK gateway 測試後，因缺 `ShioajiSdkSimulationGateway` import 失敗。
- `tests/test_simulation.py` 新增 order request builder、短 token unresolved review、store mapping callback restore、fake SDK gateway、非 simulation API 拒絕測試。
- simulation unittest：22 tests OK。
- full unittest：60 tests OK。
- compile check：OK。
- diff check：OK。
- smoke：`simulate run --execution-sync-store` 後 ingest 短 `custom_field` callback sample，再跑 restart-sync，callback 正確還原完整 `idempotency_key`，`needs_review=0`。

Grill-me review：

- 方向正確：先補 SDK-shaped gateway 與短 token mapping，再接真正 SDK callback streaming，避免 callback normalization 已有但 place order 無法符合 SDK 限制。
- must-fix 已處理：沒有硬塞完整 `idempotency_key` 進 `custom_field`；改用 6 字元 token + 本地 mapping。
- must-fix 已處理：仍維持 fake gateway / dry-run，沒有真實 Shioaji login 或 order side effect。
- 剩餘風險：尚未用真實 Shioaji simulation credentials 做 login / order smoke；下一 sprint 必須加明確人工 gate。
- 剩餘風險：目前 SDK gateway 只覆蓋股票 `Contracts.Stocks`、`Action`、`StockPriceType`、`OrderType.ROD` 與 account；盤中/盤後限制、現股/融券條件、委託 lot 尚未策略化。
- 剩餘風險：partial fill / cancelled / rejected 的 ledger lifecycle policy 尚未定義，接 streaming 前要補狀態轉移表。

### Shioaji Callback Stream MVP

目前狀態：已完成 MVP。

目標：把 Shioaji SDK 的 `set_order_callback` 接成可測 event source，收到 callback 後立即 normalize 並寫入 execution sync store；仍只允許 simulation / fake SDK。

實際產出：

- `ShioajiCallbackStream`

設計結果：

- `ShioajiCallbackStream.start()` 會註冊 `api.set_order_callback(self.handle_callback)`。
- `handle_callback(stat, msg)` 會讀取 store 內的 `custom_field_map`，呼叫 `normalize_shioaji_order_callback`，再透過 `FileExecutionSyncStore.record_callback_event` 寫入 event 與可轉換的 broker trade。
- callback stream 會累計 `callback_count`，方便 smoke / 測試確認事件有進來。
- 若 `api.simulation=False`，stream 初始化會直接拒絕，避免開發階段誤接正式區 callback。

驗證結果：

- TDD red：新增測試後，因缺 `ShioajiCallbackStream` import 失敗。
- `tests/test_simulation.py` 新增 callback stream 註冊 / event persist / 非 simulation API 拒絕測試。
- callback stream focused tests：2 tests OK。
- full unittest：63 tests OK。
- compile check：OK。
- diff check：OK。
- smoke：fake callback API emit 一筆 `OrderState.Filled` 後，stream callback count 1、callback event 1、broker trades 2、review reason 空字串。

Grill-me review：

- 方向正確：這輪只接 event source 與 store persistence，沒有做真實 login、真實委託或正式區 callback。
- must-fix 已處理：stream 與 gateway 一樣拒絕 `api.simulation=False`。
- 剩餘風險：目前 stream 只保存 callback event，尚未定義 partial fill / cancel / reject 如何改 ledger position lifecycle。
- 剩餘風險：尚未處理 callback replay ordering、duplicate callback event 去重與多進程 store locking。
- 下一步應先定義 execution lifecycle policy，再接更長時間的 gated simulation smoke。

### Execution Lifecycle Policy MVP

目前狀態：已完成 MVP。

目標：先把 Shioaji callback status 對 ledger lifecycle 的影響固定成可測 contract，避免後續 callback stream 進來後直接亂改 open positions。

實際產出：

- `ExecutionLifecycleDecision`
- `classify_execution_lifecycle`
- `FileExecutionSyncStore.lifecycle_decisions`

設計結果：

- `submitted`：`ledger_effect=none`，action=`keep_pending_order`。
- `filled`：`ledger_effect=open_position`，action=`confirm_open_position`。
- `partial_filled`：`ledger_effect=hold_for_review`，action=`partial_fill_manual_reconciliation`，reason=`partial_fill_requires_policy`。
- `cancelled` / `rejected`：`ledger_effect=close_intent`，action=`cancelled_release_intent` / `rejected_release_intent`。
- `needs_review` 或 callback 本身有 review reason：`ledger_effect=hold_for_review`，不自動修改 ledger。
- MVP 只記錄 lifecycle decision，不自動 mutation `open_positions`。

驗證結果：

- TDD red：新增 lifecycle policy 測試後，因缺 `ExecutionLifecycleDecision` import 失敗。
- `tests/test_simulation.py` 新增 filled / partial_filled / cancelled / rejected lifecycle tests。
- focused tests：4 tests OK。
- full unittest：66 tests OK。
- compile check：OK。
- diff check：OK。
- smoke：submitted / filled / partial_filled / cancelled / rejected 皆輸出預期 ledger effect 與 action。

Grill-me review：

- 方向正確：先把狀態決策寫成 pure policy 與可追蹤紀錄，避免直接把 callback 寫成不可逆 ledger mutation。
- must-fix 已處理：partial fill 不會被當成完整開倉，先進人工檢查。
- must-fix 已處理：cancelled / rejected 明確釋放 intent，但目前只記錄 decision，不直接刪 position。
- 後續已補：duplicate callback dedupe 與 callback status ordering。
- 剩餘風險：JSON file store 仍沒有 locking / transaction，多進程 callback 寫入可能競態。

### Duplicate Callback Dedupe MVP

目前狀態：已完成 MVP。

目標：避免 Shioaji callback 重送或 stream 重播時，同一筆 callback 重複寫入 execution sync store，導致 callback event、broker trade、lifecycle decision 重複。

實際產出：

- `build_callback_event_key`
- `FileExecutionSyncStore.callback_event_keys`
- `FileExecutionSyncStore.record_callback_event` 回傳 `True / False` 表示是否真的新增。

設計結果：

- callback event key 使用 `trading_date | broker_order_id | idempotency_key | normalized_status | quantity | price`。
- 同一 key 已存在時，不重複寫入 `callback_events`、`broker_trades`、`lifecycle_decisions`。
- 目前只處理完全相同 callback 的去重；不同 status 的同一 broker order 由後續 callback status ordering policy 處理。

驗證結果：

- TDD red：新增 duplicate callback 測試後，`record_callback_event` 尚未回傳 bool，測試失敗。
- focused test：1 test OK。
- full unittest：67 tests OK。
- compile check：OK。
- diff check：OK。

Grill-me review：

- 方向正確：先處理完全相同 callback 的冪等性，避免最常見的重送污染。
- must-fix 已處理：重複 callback 不會重複產生 broker trade 或 lifecycle decision。
- 後續已補：callback status ordering 已處理 out-of-order `filled -> submitted` 與同 broker order 狀態 precedence。
- 剩餘風險：JSON file store 沒有 atomic lock，多進程同時寫仍可能競態。

### Callback Status Ordering MVP

目前狀態：已完成 MVP。

目標：避免 Shioaji callback out-of-order 到達時讓 execution sync store 狀態倒退，例如 `filled` 已進來後，晚到的 `submitted` 不應再新增 broker trade 或 lifecycle decision。

實際產出：

- `build_callback_order_key`
- `callback_status_precedence`
- `FileExecutionSyncStore.callback_status_by_order`
- `FileExecutionSyncStore.callback_ordering_issues`

設計結果：

- 同一 broker order 用 `trading_date | broker_order_id | idempotency_key` 追蹤狀態。
- 狀態 precedence：`submitted < partial_filled < filled < cancelled / rejected < needs_review`。
- 若後到 callback 的 precedence 小於目前已接受狀態，`record_callback_event` 回傳 `False`，並寫入 `callback_ordering_issues`。
- stale callback 不會新增 `callback_events`、`broker_trades` 或 `lifecycle_decisions`。
- 正常 `submitted -> filled` 狀態遞進仍會被接受。

驗證結果：

- TDD red：新增 stale submitted after filled 與 submitted then filled 測試後，因缺 `callback_ordering_issues` / ordering policy 失敗。
- focused tests：2 tests OK。
- full unittest discover：69 tests OK。
- compile check：OK。

Grill-me review：

- 方向正確：這輪只補 ordering contract，仍不做真實登入、不送單，也不自動 mutation `open_positions`。
- must-fix 已處理：`filled` 後到的 `submitted` 會被跳過，不會污染 callback event、broker trade 或 lifecycle decision。
- 剩餘風險：`filled` 後的 `cancelled / rejected` 目前仍會保留，因為可能代表更複雜的 broker 狀態，需要下一輪用 manual review / terminal-state policy 補強。
- 後續已補：execution sync store locking / atomic write 已處理單機多 writer 競態。

### Execution Sync Store Locking MVP

目前狀態：已完成 MVP。

目標：避免多個 Shioaji callback writer 同時寫入 execution sync JSON store 時互相覆蓋，或讓讀取端看到半寫入 JSON。

實際產出：

- `FileExecutionSyncStore.lock_path`
- `FileExecutionSyncStore._mutate_snapshot`
- `FileExecutionSyncStore._exclusive_lock`
- `record_result` 與 `record_callback_event` 改為在同一個 locked read-modify-write 區段內更新 snapshot。
- `_write_snapshot` 改為同目錄 temp file + `os.replace` atomic replace。

設計結果：

- lock 檔路徑為 execution store path 加上 `.lock` suffix。
- file lock 包住整個 read-modify-write，不只鎖寫入，避免兩個 writer 都從舊 snapshot 開始修改。
- temp file 以 process id 與 uuid 命名，降低同 process 多 thread / 多 process 撞名風險。
- 仍維持 JSON file store contract；尚未引入 DB-backed repository。

驗證結果：

- TDD red：新增 30 個 thread 同時寫 callback 的測試後，原本會出現 `JSONDecodeError` / lost write。
- focused concurrency test：1 test OK。
- full unittest discover：70 tests OK。
- compile check：OK。

Grill-me review：

- 方向正確：先補本階段 JSON store 的最小可用並發保護，沒有過早引入 DB / queue / event bus。
- must-fix 已處理：callback / result 的 read-modify-write 都進 lock，JSON 寫入也改成 atomic replace。
- 剩餘風險：file lock 是單機單檔保護；若之後變成多台機器、NFS 或真正高頻 callback，應升級為 DB-backed repository 或 append-only event log。
- 後續已補：terminal-state policy 已攔截 `filled / cancelled / rejected` 之間的終態衝突。

### Terminal-State Policy MVP

目前狀態：已完成 MVP。

目標：避免同一 broker order 已經進入 `filled / cancelled / rejected` 終態後，又收到另一個不同終態時，被當成正常狀態遞進寫入 broker trade 或 lifecycle decision。

實際產出：

- `is_terminal_callback_status`
- `FileExecutionSyncStore.record_callback_event` terminal-state conflict gate
- `callback_ordering_issues.reason = terminal_state_conflict`

設計結果：

- terminal callback status：`filled`、`cancelled`、`rejected`。
- 若同一 broker order 目前狀態已是 terminal，且 incoming status 也是另一個 terminal，則視為 `terminal_state_conflict`。
- terminal-state conflict 會寫入 `callback_ordering_issues`，`record_callback_event` 回傳 `False`。
- terminal-state conflict 不會新增 `callback_events`、`broker_trades` 或 `lifecycle_decisions`。
- `filled -> submitted` 仍維持 stale callback status，不混同為 terminal conflict。

驗證結果：

- TDD red：新增 `filled -> cancelled` 測試後，原本會被接受為正常狀態遞進。
- focused ordering tests：3 tests OK。
- full unittest discover：71 tests OK。
- compile check：OK。

Grill-me review：

- 方向正確：終態衝突先進人工檢查，不自動覆寫 ledger / broker lifecycle。
- must-fix 已處理：`filled` 後到的 `cancelled` 不再新增 broker trade 或 lifecycle decision。
- 剩餘風險：目前只處理 callback status contract；真正長時間 simulation smoke 還沒建立，下一步需要用 fake stream 或 gated simulation smoke 驗證多事件序列。

### Longer Callback Smoke MVP

目前狀態：已完成 MVP。

目標：用一串 callback payload 驗證 execution sync store 的多事件序列，不只測單筆 callback。這是進入 gated Shioaji simulation smoke 前的本地安全檢查。

實際產出：

- `run_callback_sequence_smoke`
- CLI：`tw-daytrade simulate callback-smoke`
- sample：`examples/shioaji-callback-sequence.sample.json`

設計結果：

- callback smoke input 可為 JSON list，或 `{ "callbacks": [...] }`。
- 每筆 callback 會依目前 store 的 `custom_field_map` normalize，再呼叫 `record_callback_event`。
- report 會輸出 summary：`total / accepted / skipped`。
- report 會輸出逐筆 event 摘要與 `callback_ordering_issues`。
- sample sequence 覆蓋 `submitted -> filled -> stale submitted -> terminal conflict cancelled`。

驗證結果：

- TDD red：新增 sequence smoke 測試後，因缺 `run_callback_sequence_smoke` 失敗。
- focused smoke tests：2 tests OK。
- full unittest discover：73 tests OK。
- compile check：OK。
- CLI smoke：sample sequence 4 events，accepted 2、skipped 2，ordering issues 為 `stale_callback_status` 與 `terminal_state_conflict`。

Grill-me review：

- 方向正確：先用本地 callback sequence smoke 覆蓋多事件順序，不直接跳真實 Shioaji smoke。
- must-fix 已處理：smoke report 會同時揭露 accepted / skipped 與 ordering issues，方便判斷終態衝突是否被擋下。
- 剩餘風險：尚未跑真正 Shioaji simulation callback；下一步必須保持人工 gate，且只允許 `simulation=True`。

### Gated Shioaji Simulation Smoke Guard MVP

目前狀態：已完成 MVP。

目標：往真正 Shioaji simulation smoke 前進，但先把 gate 做牢。預設執行不 import Shioaji、不登入、不送單，只輸出 blocked report；只有明確啟用 gate 才能做 simulation login 或 callback registration smoke。

實際產出：

- `run_gated_shioaji_simulation_login_smoke`
- `run_gated_shioaji_callback_stream_smoke`
- CLI：`tw-daytrade simulate shioaji-smoke`
- `ShioajiSdkSimulationGateway` login flags：`fetch_contract` / `subscribe_trade` 可由 smoke 控制

設計結果：

- `simulate shioaji-smoke` 預設會產生兩個 blocked report：login smoke 與 callback stream smoke。
- login smoke 必須帶 `--enable-login-smoke`，才會建立 `sj.Shioaji(simulation=True)`。
- credentials 只從 env name 讀取，不寫進 report。
- callback stream smoke 必須帶 `--enable-callback-stream --store ...`，且需要先啟用 login smoke。
- smoke report 明確列出 side effects；目前允許的 side effect 只有 `login` 與 `set_order_callback`，不包含 order。

驗證結果：

- focused gated smoke tests：4 tests OK。
- full unittest discover：77 tests OK。
- compile check：OK。
- CLI blocked smoke：summary total 2、ok 0、blocked 2。
- 真實 Shioaji simulation login smoke：已通過，summary total 2、ok 1、blocked 1；side effect 只有 `login`，`orders_allowed=false`。
- 真實 Shioaji callback registration smoke：已通過，summary total 2、ok 2、blocked 0；side effect 為 `login` 與 `set_order_callback`，store callback events / broker trades / lifecycle decisions 皆為 0。

Grill-me review：

- 方向正確：先建立真實 smoke 的安全開關與報告格式，不直接跳入登入或送單。
- must-fix 已處理：預設 CLI 不 import Shioaji、不讀 credential、不登入；明確 gate 後也只允許 `simulation=True`。
- 剩餘風險：目前只驗證 login 與 callback registration，尚未送出 simulation order；下一步若做 order request smoke，必須先定義最小委託、盤中 / 盤後限制、取消 / retry 行為與人工 gate。

### Gated Shioaji Order Request Smoke Guard MVP

目前狀態：已完成 MVP，尚未跑真實 Shioaji order smoke。

目標：把下一步「真的送出一筆 Shioaji simulation order」的 gate 與驗證鏈先做起來。這一步仍以 fake gateway / 本地測試驗證，不自動對外送單。

實際產出：

- `run_gated_shioaji_order_request_smoke`
- CLI：`tw-daytrade simulate shioaji-smoke --enable-order-smoke`
- order smoke report：`result`、`store_summary`、`restart_sync`

設計結果：

- 預設 `simulate shioaji-smoke` 會多一個 `shioaji_order_request` blocked report。
- order smoke 必須同時滿足：
  - `--enable-login-smoke`
  - 成功 simulation login smoke
  - `--enable-order-smoke`
  - `--store`
  - `--input`
  - risk decision approved
  - limit price present
  - quantity 在 `--max-order-quantity` 內
- order smoke 的 side effects 明確標成 `login`、`place_order`。
- 成功後會把 result 寫入 `FileExecutionSyncStore`，並立即用 restart-sync summary 檢查 broker trade / open position 是否 matched。

驗證結果：

- focused gated order smoke tests：4 tests OK。
- full unittest discover：80 tests OK。
- compile check：OK。
- diff check：OK。
- CLI blocked smoke v2：summary total 3、ok 0、blocked 3。

Grill-me review：

- 方向正確：這輪只補送單前的 gate、防呆與本地 reconciliation，不直接跑真實 simulation order。
- must-fix 已處理：無 gate 不 login / 不 place order；market order 被擋；超過 smoke quantity limit 會被擋；成功 fake order 後會檢查 restart-sync matched。
- 剩餘風險：真正 simulation order 仍可能因盤中 / 盤後、契約載入、帳戶狀態、價格限制或 SDK response shape 不同而失敗；下一步實測前要先定義測試委託與取消 / retry 策略。

### Order Smoke Session Gate / Cancel Guard MVP

目前狀態：已完成 MVP，尚未跑真實 Shioaji order 或 cancel smoke。

目標：補上真實 simulation order smoke 前最後兩個保護：盤中 / 盤後 time gate，以及 cancel smoke 的可測 gate。這輪仍以 fake gateway 測試，不自動對外送單或取消。

實際產出：

- `is_regular_day_order_smoke_time`
- `run_gated_shioaji_cancel_smoke`
- `ShioajiCancelGateway`
- `ShioajiSdkSimulationGateway.cancel_order`
- CLI：`--allow-outside-session`、`--current-time`、`--enable-cancel-smoke`、`--cancel-broker-order-id`

設計結果：

- order smoke 預設只允許 `09:00-13:20`。
- `--allow-outside-session` 必須明確指定，盤後 / 非常規時段才可繼續 order smoke。
- cancel smoke 預設 blocked。
- cancel smoke 需要 `--enable-cancel-smoke` 與 `--cancel-broker-order-id`。
- 預設 `simulate shioaji-smoke` 會輸出 login / callback / order / cancel 四個 blocked reports。

驗證結果：

- focused session gate / cancel guard tests：6 tests OK。
- full unittest discover：84 tests OK。
- compile check：OK。
- diff check：OK。
- CLI blocked smoke v3：summary total 4、ok 0、blocked 4。

Grill-me review：

- 方向正確：先補時段與取消 gate，不急著真實送 simulation order。
- must-fix 已處理：盤後 order smoke 預設會擋；缺 cancel broker order id 不會進 gateway；fake cancel 可驗證 side effect 與 result shape。
- 剩餘風險：`ShioajiSdkSimulationGateway.cancel_order` 目前只定義最小邊界，真實 SDK 可能需要 Trade object 而不是 broker order id；下一步實測時要確認 SDK response shape，再決定是否保存 raw trade handle 或建立 cancel mapping。

### Shioaji Order Handle Persistence / Cancel Mapping MVP

目前狀態：已完成 MVP，尚未跑真實 Shioaji order 或 cancel smoke。

目標：補上真實 simulation order 後取消所需的資料保存。若 Shioaji SDK 取消需要 raw Trade object / response handle，而不是 broker order id，系統需要先把 place order response 保存起來。

實際產出：

- `FileExecutionSyncStore.shioaji_order_handles`
- `FileExecutionSyncStore.cancel_results`
- `FileExecutionSyncStore.record_order_handle`
- `FileExecutionSyncStore.record_cancel_result`
- `ShioajiOrderRequestBroker.last_request`
- `ShioajiOrderRequestBroker.last_response`
- cancel smoke 會從 store 讀取 order handle，並傳給 gateway cancel boundary

設計結果：

- order smoke 成功後，除了原本的 result / broker trade / open position，還會保存 raw order response。
- cancel smoke 若找到 broker order id 對應的 `shioaji_order_handles`，會優先使用該 handle。
- cancel response 會保存到 `cancel_results`，方便後續 close report 或 callback reconciliation 使用。
- raw handle 會先轉成 JSON-safe payload，避免 SDK object 直接寫入 JSON store 失敗。
- CLI 預設仍 blocked，不送單、不取消。

驗證結果：

- focused raw handle / cancel mapping tests：3 tests OK。
- full unittest discover：85 tests OK。
- compile check：OK。
- diff check：OK。
- CLI blocked smoke v4：summary total 4、ok 0、blocked 4。

Grill-me review：

- 方向正確：先補保存與映射，不靠 broker order id 猜真實 SDK cancel 行為。
- must-fix 已處理：fake order response raw handle 能保存；SDK-like object 會轉成 JSON-safe payload；cancel smoke 能讀取 handle；cancel result 能寫回 store。
- 剩餘風險：JSON-safe payload 不一定等同 SDK 取消所需的 live Trade object；真實 smoke 若需要 live object，應建立 in-memory session-scoped cancel map。

### P6D Gated Simulation Order / Cancel Smoke Complete

目前狀態：已完成。

目標：在人工 gate 下跑真實 Shioaji `simulation=True` order / callback / cancel smoke，確認 P6 執行鏈路不是只停在 fake SDK。

實際結果：

- 真實 simulation login：OK。
- callback registration：OK。
- simulation order：OK，測試委託為 2330 buy 1000 股、limit price 2120，狀態 `submitted`。
- callback ingestion：OK，收到 `New -> submitted` 與 `Cancel -> cancelled` callback，且 idempotency key 可由 6 字元 custom field mapping 還原。
- cancel smoke：OK，同一輪使用 live raw Trade handle 完成 cancel。
- restart-sync：matched 2、needs_review 0。

最終 smoke report：

- `reports/2026-06-02-p6-complete-smoke-v2.json`
- summary：total 4、ok 4、blocked 0
- broker order id：已保存於本地 report/store，不在外部回報暴露更多帳戶細節。

驗證結果：

- focused real-payload / submitted / handle tests：4 tests OK。
- full unittest discover：89 tests OK。
- compile check：OK。
- diff check：OK。

Grill-me review：

- 方向正確：P6 只驗證 Shioaji simulation 執行鏈路，沒有碰正式區，沒有把 simulation 結果混進 strategy edge。
- must-fix 已處理：真實 callback payload 的 dict / operation shape 已修正；`PendingSubmit` 不再被當成 filled；submitted 不建立 open position；cancel 使用 live Trade handle。
- 剩餘風險：P6 尚未處理 production readiness，包括正式 gate、交易時段策略、partial fill、cancel retry、失敗告警、人工確認流程與正式下單權限控管。這些應進下一 phase，不再塞進 P6。

### P7 Production Readiness Gate Complete

目前狀態：已完成。

目標：把 P6 後留下的 production readiness gap 收斂成一個可測、可擋、可回報的正式營運前 gate，而不是直接啟用正式下單。

實際結果：

- `simulate production-readiness` 已完成，可從 execution sync store 產生 JSON / Markdown readiness report。
- readiness checks 包含：
  - `formal_live_gate`
  - `regular_session_policy`
  - `pending_order_limit`
  - `partial_fill_policy`
  - `callback_ordering`
  - `cancel_retry_plan`
- live execution 預設 blocked；必須明確 `--allow-live-trading` 且提供符合 expected token 的 `--manual-approval-token`，readiness report 才可能進入 `ready`。
- pending submitted order、partial fill、callback ordering issue、cancel retry 缺口都會進 alerts 與 manual actions。
- P7 命令本身不登入、不送單、不取消，只做正式營運前判斷。

P7 smoke：

- input store：`reports/2026-06-02-p6-complete-smoke-store-v2.json`
- output JSON：`reports/2026-06-02-p7-production-readiness.json`
- output Markdown：`reports/2026-06-02-p7-production-readiness.md`
- result：checks 6、ok 5、blocked 1、needs_review 0。
- 唯一 blocker：`formal_live_gate`，原因是未提供正式人工 approval token。
- pending orders：0。
- partial fills：0。
- ordering issues：0。

驗證結果：

- focused P7 tests：5 tests OK。
- full unittest discover：94 tests OK。
- compile check：OK。
- diff check：OK。

Grill-me review：

- 方向正確：P7 只做正式營運前 gate/report/alert contract，沒有偷開正式區，也沒有把 readiness 當成策略 edge。
- must-fix 已處理：live gate 預設 blocked；人工 token 才能讓 report ready；pending / partial / ordering / cancel retry 都可被 report 揭露。
- should-fix：下一 phase 不是直接正式下單，而是先做 P8 simulation ops go-live；後面再接 P9 reports / alerts、P10 regression correction loop、P11 live execution design。
- 結論：P7 完成；不要再拆 P7。

### Remaining Phase Map

目前剩餘 4 個 phase。

P8 Simulation Ops Go-live：

- 把 P6/P7 的 gated simulation order chain 變成每日可執行的 simulation ops。
- 仍只使用 Shioaji simulation，不啟用正式區。
- 產出 execution bundle：input plan、order report、callback store、restart-sync、readiness report。
- 驗收：一個交易日可完成 end-to-end simulation run，且所有 side effects / blocked reasons / broker order ids 可追蹤。

P9 Ops Reports / Alerts：

- 把 candidate / replay / simulation / restart-sync / readiness 合成 daily ops close report。
- Telegram summary 從 dry-run 推進到 gated send；預設仍 blocked。
- report 必須列 pending orders、partial fills、callback ordering issues、readiness blockers、manual actions。
- 驗收：一個命令可產出 operator-ready report，並可在明確 gate 下發送摘要。

P10 Regression Correction Loop：

- 將每日 simulation ops 的錯誤、mismatch 與 missed cases 轉成 regression cases。
- 分類 data issue、candidate quality、risk decision、broker/callback lifecycle、reporting issue。
- 自動產生 replay / simulation regression fixture。
- 驗收：每日問題能進 regression backlog，且可由 tests / smoke command 驗證已修正。

P11 Live Execution Design：

- 在 simulation ops 穩定後才設計正式區下單。
- 定義 live approval token、live broker adapter、權限控管、部位上限、daily stop、panic cancel、kill switch、正式告警與人工確認 SOP。
- 驗收：即使 live adapter 存在，沒有正確 approval token / session gate / readiness ready 仍不能送單。

### P8-P11 Grill-me Review

Verdict：方向正確，但目前 P8-P11 還是 phase headline，不是足夠抗失敗的營運規格。最大風險不是缺功能，而是把「simulation order 可以送出」誤判為「每日營運可以穩定跑」。

Most likely project failure points：

1. P8 沒有明確定義 input plan 來源。
   - 現在寫 candidate -> risk decision -> simulation order，但 `candidate` 如何變成 approved `RiskDecision` 還沒定義。
   - 若 P8 直接拿 sample plan 或手工 plan 跑，會造成 simulation ops 看似上線，實際上沒有驗證候選與風控決策鏈。
   - must-fix：P8 要明確定義 plan builder contract，至少包含 candidate source、risk decision reason、limit price source、quantity source、blocked reason。

2. P8 沒有把價格限制 / 交易時段 / contract readiness 變成第一級 gate。
   - P6 真實 smoke 已經遇過價格超過漲跌幅，這不是小 bug，是每日 ops 最容易翻車的入口。
   - must-fix：P8 runner 必須在送 simulation order 前檢查 limit_up / limit_down、reference price、contract loaded、regular session、trading day。

3. P8 如果沒有 run bundle schema，後面 regression 會無法追。
   - 只產生很多 report path 不夠；每個 daily run 需要唯一 run id、artifact manifest、input/output checksum、side effects summary。
   - must-fix：P8 要產生 `ops_run_manifest`，把 input plan、order report、callback store、restart-sync、readiness、logs 連起來。

4. P9 的 alert 如果沒有 severity / owner / dedupe，會變成噪音。
   - Telegram summary gated send 不等於可營運告警。
   - must-fix：P9 要定義 alert severity、dedupe key、send gate、人工處理狀態、重送限制。

5. P10 regression correction 太容易變成「事後寫文件」，不是修正迴路。
   - failure classification 不夠，必須能產生可重跑 fixture，並要求修正前紅燈、修正後綠燈。
   - must-fix：P10 每個 regression case 必須有 source run id、failure type、minimal fixture、expected behavior、closing test command。

6. P11 最大風險是太早碰正式區。
   - live execution design 應有 entry criteria，不能只因 P8-P10 做完就開。
   - must-fix：P11 前置條件應包含連續 N 個 trading days simulation ops 無 blocker、無 unresolved partial fill / ordering issue、report 準時送達、regression backlog 無 P0/P1 open items。

7. 整體 roadmap 還沒明確區分「能營運」與「有策略 edge」。
   - Simulation ops 穩定只能證明執行鏈可控，不證明賺錢。
   - must-fix：P8-P11 文件要持續保留這句邊界：任何 simulation / live readiness 都不得被當成 strategy expectancy 證明。

Fix priority：

- Must fix before P8 implementation：plan builder contract、price/contract/session gates、ops run manifest。
- Must fix during P9：alert severity / dedupe / owner / gated send。
- Must fix during P10：regression case schema 與 red-green verification。
- Must fix before P11：live entry criteria 與 kill switch / panic cancel / daily stop SOP。

Better version of phase gates：

- P8 is not done when an order can be sent; P8 is done when one daily simulation ops run can be replayed, audited, and explained from manifest alone.
- P9 is not done when a Telegram message is sent; P9 is done when the alert tells the operator what broke, who owns it, whether it was already sent, and what action closes it.
- P10 is not done when a bug is documented; P10 is done when the same failure becomes a regression fixture that fails before the fix and passes after.
- P11 is not allowed to start live execution until simulation ops has stable-day evidence and unresolved P0/P1 operational risks are zero.

### P0-P11 Full Grill-me Review

Verdict：整體 phase order 是對的，但目前最容易失敗的地方是「完成的 phase 被後續誤當成可靠基礎」。P0-P7 已完成的是 MVP / gate / smoke，不是完整 production proof；P8-P11 必須把 residual risks 當作輸入，而不是把它們當作已消失。

Most likely full-project failure points：

1. P0 bootstrap 缺少安裝 / packaging / entrypoint smoke 的長期保證。
   - 風險：CLI 在 repo 內能跑，不代表排程或別的 shell 環境能跑。
   - correction：P8 ops runner 要明確記錄 Python path、cwd、env file source、command line 與 version / commit hash。

2. P1 old log importer 證明了舊錯誤可分類，但舊資料 schema drift 仍可能讓 regression 偏掉。
   - 風險：後續 P10 若只靠單一 sample schema，會漏掉新型態錯誤。
   - correction：P10 regression case 必須保存 raw source row / payload，不只保存 normalized result。

3. P2 TiDB integration 是 MVP，不是 migration system。
   - 風險：P8-P10 若大量寫入 ops artifacts，schema drift / 重跑 / migration 會變成隱性風險。
   - correction：P9/P10 若要保存 ops result，必須先定義 migration/version strategy，不要直接擴張現有 schema。

4. P3/P4/P4b candidate pipeline 仍缺「候選品質回饋」閉環。
   - 風險：P8 每天能送 simulation order，但送的是低品質 candidate，最後只得到穩定執行低品質交易。
   - correction：P9 report 必須把 candidate source、downgrade reasons、data gaps 與 simulation outcome 放在同一份 ops report；P10 要能把 candidate quality failure 回寫成 regression case。

5. P5 replay 仍依賴 valid sample quality 與 cost assumptions。
   - 風險：後續把 replay output 當成策略 edge 時，會高估系統。
   - correction：P9/P10 report 必須繼續分離 replay expectancy、simulation execution health、live readiness，不可合成單一健康分數來誤導。

6. P6 真實 simulation smoke 只驗證一筆 order/cancel。
   - 風險：單筆 2330 limit order/cancel 成功，不能代表多標的、多價格、不同交易狀態、partial fill、異常 callback 都穩。
   - correction：P8 不可把 P6 smoke 當 production proof；P8 要做 daily runner + manifest，P10 要把新的 broker/callback shape 轉 regression fixture。

7. P7 readiness report 是 advisory contract，不是硬性 broker enforcement。
   - 風險：後續若 live adapter 只讀 P7 report 的 `live_execution_allowed`，會形成單點誤判。
   - correction：P11 live adapter 必須自己重新驗證 approval token、session gate、risk limits、readiness state，不可只信 report。

8. P7 approval token 預設值可預測。
   - 風險：如果沿用 `{date}:LIVE-TRADING-APPROVED` 到正式流程，gate 等於假 gate。
   - correction：P11 必須改為不可預測、短效、可稽核的人工 approval token；P7 預設 token 僅限測試。

9. P8-P11 仍缺明確 entry / exit criteria。
   - 風險：phase 看似完成，但實際只完成命令或文件，未達到可營運。
   - correction：每個 phase close-out 必須列出 command gate、artifact gate、regression gate、operator gate。

10. 最大系統性風險：把 execution stability 誤認為 strategy profitability。
    - 風險：系統穩定送 simulation order，但策略本身沒有 edge，仍會導向錯誤決策。
    - correction：所有 report 都必須保持三層分離：strategy evidence、execution health、operational readiness。

Corrected phase guardrails：

- P8 must start with plan builder contract + price/contract/session gates + ops run manifest before any daily runner expansion.
- P9 must not send alerts until alert severity / dedupe / owner / manual action are defined.
- P10 must require raw source payload + minimal fixture + closing test command for every regression case.
- P11 must enforce approval / readiness / risk limits at broker boundary, not only in reports.
- P0-P7 residual risks must remain visible in P8-P11 documents; completed phase means usable baseline, not proof of production safety.

## 5. Working Commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build --date 2026-05-28 --input examples/candidates.sample.json
PYTHONPATH=src python3 -m tw_day_trading_lab.cli report daily --date 2026-05-28 --input reports/2026-05-28-candidates.json --format md
PYTHONPATH=src python3 -m tw_day_trading_lab.cli notify telegram --date 2026-05-28 --report reports/2026-05-28-daily.md --dry-run
PYTHONPATH=src python3 -m tw_day_trading_lab.cli old-logs import --date 2026-03-25 --input examples/old-log.sample.csv --output reports/old-log-sample-samples.json --report-output reports/old-log-sample-failure.md
PYTHONPATH=src python3 -m tw_day_trading_lab.cli db init --schema sql/001_init.sql
PYTHONPATH=src python3 -m tw_day_trading_lab.cli samples persist --input reports/old-log-sample-samples.json
PYTHONPATH=src python3 -m tw_day_trading_lab.cli samples summary
PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates persist --date 2026-05-28 --input reports/2026-05-28-candidates.json --run-id p2-smoke-2026-05-28 --source sample-fixture --status generated
PYTHONPATH=src python3 -m tw_day_trading_lab.cli ingest finmind --date 2026-05-28 --requests examples/finmind.requests.sample.json --cache-dir data/raw
PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build-from-raw --date 2026-05-28 --cache-dir data/raw --output reports/2026-05-28-candidates-from-raw.json
PYTHONPATH=src python3 -m tw_day_trading_lab.cli report daily --date 2026-05-28 --input reports/2026-05-28-candidates-from-raw.json --format md --output reports/2026-05-28-daily-from-raw.md
PYTHONPATH=src python3 -m tw_day_trading_lab.cli replay samples --date 2026-03-25 --input reports/old-log-sample-samples.json --output reports/2026-03-25-replay.json --report-output reports/2026-03-25-replay.md --cost-r 0.1
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate run --date 2026-05-28 --input examples/simulation-plan.sample.json --output reports/2026-05-28-simulation.json --report-output reports/2026-05-28-simulation.md
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate callback-smoke --date 2026-05-28 --input examples/shioaji-callback-sequence.sample.json --store reports/2026-05-28-callback-smoke-store.json --output reports/2026-05-28-callback-smoke.json
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-smoke --date 2026-05-28 --output reports/2026-05-28-shioaji-smoke.json
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-smoke --date 2026-06-02 --enable-login-smoke --enable-callback-stream --store reports/2026-06-02-shioaji-callback-smoke-store.json --output reports/2026-06-02-shioaji-callback-stream-smoke.json
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-smoke --date 2026-06-02 --enable-login-smoke --enable-order-smoke --input examples/simulation-plan.sample.json --store reports/2026-06-02-shioaji-order-smoke-store.json --output reports/2026-06-02-shioaji-order-smoke.json --max-order-quantity 1000
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-smoke --date 2026-06-02 --enable-login-smoke --enable-order-smoke --input examples/simulation-plan.sample.json --store reports/2026-06-02-shioaji-order-smoke-store.json --output reports/2026-06-02-shioaji-order-smoke.json --max-order-quantity 1000 --allow-outside-session
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-smoke --date 2026-06-02 --enable-login-smoke --enable-callback-stream --enable-order-smoke --enable-cancel-smoke --fetch-contract --subscribe-trade --input reports/2026-06-02-p6-real-order-plan-low.json --store reports/2026-06-02-p6-complete-smoke-store-v2.json --output reports/2026-06-02-p6-complete-smoke-v2.json --max-order-quantity 1000
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate production-readiness --date 2026-06-02 --store reports/2026-06-02-p6-complete-smoke-store-v2.json --output reports/2026-06-02-p7-production-readiness.json --report-output reports/2026-06-02-p7-production-readiness.md --current-time 12:30
PYTHONPATH=src python3 -m tw_day_trading_lab.cli report close --date 2026-05-28 --candidates reports/2026-05-28-candidates-from-raw.json --replay reports/2026-03-25-replay.json --simulation reports/2026-05-28-simulation.json --output reports/2026-05-28-close.md --telegram-summary-output reports/2026-05-28-telegram-summary.txt
PYTHONPATH=src python3 -m tw_day_trading_lab.cli notify telegram --date 2026-05-28 --report reports/2026-05-28-close.md --dry-run
```

## 6. Backlog

- 精確定義交易成本、滑價與最小成交金額。
- 定義 `next_day_actionable` 數學條件 v2。
- 設計 TiDB schema migration 流程。
- 決定 raw data 儲存先用 JSONL 還是直接導入 Parquet library。
- 設計 Telegram 正式發送 gate。
- P8 Simulation Ops Go-live。
- P9 Ops Reports / Alerts。
- P10 Regression Correction Loop。
- P11 Live Execution Design。

## 7. Documentation Close-out - 2026-06-03

本輪目標：將 P8-P11 must-fix、P0-P11 full grill-me review 與目前專案狀態同步到 README / docs，作為推上 GitHub 前的文件基準。

已完成文件更新：

- `README.md`：補 Remaining Phase Map、P8 前 must-fix、Documentation Map，並明確標示下一步是 P8 daily simulation ops go-live，不是繼續拆 P7 或直接正式下單。
- `docs/data-contracts.md`：補 P8 `ops_run_manifest`、P9 alert、P10 regression case 的契約草案，讓後續實作有可驗收邊界。
- `docs/development-work.md`：保留 P8-P11 grill-me review、P0-P11 full review，並補本 close-out 紀錄。

Grill-me close-out verdict：

- 方向正確：文件已把 P8-P11 的失敗點轉成具體契約，避免後續只做「能跑命令」的假完成。
- must-fix 已寫回：plan builder contract、price / contract / session gates、ops manifest、alert severity / dedupe / owner、regression raw payload / closing test、P11 live boundary enforcement。
- 剩餘風險：這仍是文件與契約層，尚未完成 P8 實作；下一輪 coding 應先從 `ops_run_manifest` 與 plan builder contract 開始。
- 下一步：P8 Simulation Ops Go-live 第一刀，先做 manifest + input plan builder + pre-order gates，再擴 daily runner。

## 8. Phase Close-out - P8 Simulation Ops Go-live - 2026-06-03

已完成 P8 Simulation Ops Go-live 實作。

### 達成項目：

1. **`ops_run_manifest`**:
   - 定義並生成單次執行的唯一 `run_id`。
   - 自動記錄執行命令（已遮蔽敏感資訊）、當前工作目錄 `cwd`、當前 Git commit hash、環境變數摘要（不含變數值）。
   - 自動計算輸入與輸出 Artifacts 的 SHA-256 Checksum。
   - 記錄執行期間的 Side Effects（如 `login`, `place_order_2330` 等）與 Blocked Reasons（被 Gates 阻擋的原因）以及由 Production Readiness Check 產出的 Manual Actions。

2. **Input Plan Builder Contract**:
   - 實作 `build_simulation_plan` 函數，負責將 `CandidateScore` 列表與 FinMind 價格快照結合。
   - 自動查驗候選股是否為 `next_day_actionable`，查驗快照中是否具有昨日收盤價，將其標註為 `limit_price_source="close_price"`，並完整記載 `risk_decision_reason`、`quantity_source`、與 `blocked_reason`。

3. **Pre-order Gates**:
   - 在 `check_pre_order_gates` 函數中實作了 7 項 Pre-order gates：
     - **Trading Day Gate**: 阻擋非交易日 / 周末委託。
     - **Regular Session Gate**: 檢查委託時間是否在 `09:00 - 13:20` 的當沖常規時段內。
     - **Contract Loaded Gate**: 驗證股票 Contract 正常載入。
     - **Reference Price Check**: 取得參考價。
     - **Limit Up / Limit Down Gate**: 確保委託價在漲跌停限制內。
     - **Quantity Cap Gate**: 限制委託數量（預設 1000 股以內）。
     - **Simulation-Only Boundary Gate**: 確保僅限模擬模式（不接觸正式交易區）。

4. **CLI Subcommand `simulate ops-run`**:
   - `tw-daytrade simulate ops-run` 命令整合了從載入 candidates -> 建 plan -> 檢查 gates -> Adapter 執行 -> 記錄 sync state -> 產出 readiness report -> 生成 manifests 的完整 Daily Ops Flow。
   - 新增 3 個單元測試覆蓋了 gates、plan builder 與 `ops-run` CLI 功能，所有測試全數通過且無 any 格式/排版警告。

## 9. Phase Close-out - P9-P11 Infrastructure Hardening - 2026-06-03

### 達成項目：
- **P9: Ops Reports / Alerts**: 實作每日 Close Report 整合 candidate, replay, simulation, restart-sync, readiness checks。實作 `generate_alerts_from_run` 並輸出 `alerts.json`。實作 Telegram gated 發送。
- **P10: Regression Loop**: 實作 `RegressionCase` 資料結構，並新增 `regression-import` 及 `regression-run` CLI 指令，實現測試回歸閉環。
- **P11: Live Gateway Boundary**: 實作 `LiveShioajiBrokerAdapter` 與其嚴格門禁驗證（允許交易開關、熵值足夠的 approval token 驗證及 regression loop 中無 open items 等）及 panic cancel 等安全措施。

## 10. Phase Close-out - Phase B: Resolving Six Core Problems - 2026-06-03

### 達成項目：
- **B1: Realistic Cost & Slippage Model**: 實作 `TaiwanDayTradeCostModel` 與其來回成本 R 單位換算，整合至 `ReplayAssumptions` 與 `replay_one_sample`，並新增 `cost-analysis` 命令。
- **B2: Risk Management & Exit Engine**: 實作 Fixed Fractional Position Sizing 與其捨去千股、權益上限檢查；實作 intraday exit 檢查功能（Stop Loss, Take Profit, Time Stop 13:20）；實作持倉上限 (3) 與總曝險限制 (6%)。
- **B3: Microstructure Gates**: 實作跌停賣出與漲停買入微結構限制、融券做空限制、13:25-13:30 收盤集合競價限制。
- **B4: Backtest Methodology Upgrades**: 強制執行 `candidate_date < trading_date`，計算 95% 信賴區間與樣本數警告，並實作 `walk-forward` 滾動驗證 CLI 工具。
- **B5: Performance Feedback Loop**: 實作 `RollingPerformanceTracker` 控制，當 rolling expectancy 為負或 Drawdown 超限時自動調降 Quantity 或停用策略。
- **B6: Strategy Edge Redesign**: 實作 `VwapBreakoutStrategy` 行情訊號產生器與 ATR 波動度過濾機制。

新增 8 個測試覆蓋了以上所有 B1-B6 之邏輯，所有 108 個單元測試全數通過，且 `git diff --check` 完全通過。

## 11. Discussion Consensus Documentation - 2026-06-03

針對當前架構在「候選股產生流程」、「流動性失真分析」以及「自動化執行防呆」的討論，已彙整共識並更新至 [six-problems-review-and-ops.md](file:///home/hom/services/stock/tw-day-trading-lab/docs/six-problems-review-and-ops.md)。

### 核心共識與文件更新：
1. **多維度流動性過濾**：闡明單純以成交金額過濾會造成「高價低量股假流動性」與「低價高量股被誤殺」的失真現象，建議改用 20 日均成交金額 (20-day ADV) 結合成交張數與 Spread% 作為 Universe 篩選指標。
2. **自動化防呆運行**：釐清自動化流程之觸發時序（Ingestion -> Candidate Build -> Ops Run Plan -> Close Report），並詳細定義 Git Clean Check、Lock File 機制、Checksum/Manifest 軌跡記錄、資料完整性門禁等生產環境安全要求，以硬化自動化執行鏈。
3. **自動化環境變數與憑證加載優化**：程式已重構，支持在 CLI 入口點自動調用 `python-dotenv` 加載專案根目錄下的 `.env` 檔案；並在 Shioaji 模擬登入時，自動偵測並調用 `activate_ca` 方法啟用本地的 `Sinopac.pfx` 憑證（支援環境變數 `CERT_PATH` / `CA_PASSWORD` / `CA_ID`），無須再在 CLI 前手動附加複雜的 `env` 宣告指令。

## 12. Post-Market Hardening Sprints - 2026-06-03

### Sprint 3-A: 多維度流動性過濾升級

目前狀態：已完成。commit `658c02c`

- **20日均成交金額 (ADV-20d) filter**：在 `build_candidates_from_raw_cache` 中新增 `min_adv_20d_money=50_000_000` 參數，計算最近 20 根 K 棒的 `Trading_money` 平均；若低於門檻則過濾，summary 記錄 `filtered_low_adv`。防禦單日新聞暴量假流動性。
- **最低成交張數 filter**：新增 `min_volume_lots=500` 參數（1 張 = 1000 股），目標日成交量不足 500 張則過濾，summary 記錄 `filtered_low_volume_lots`。防禦高價低量股的委託簿稀疏問題。
- **高價股 Spread% 警告**：close > 500 且 intraday_range_pct < 0.3% 時，summary 記錄 `warned_high_price_spread`（不過濾，僅警告）。
- 新增 3 個單元測試，全數通過，總計 111 tests。

殘餘風險：
- ADV-20d 使用 JSONL 現有筆數；若 cache < 20 筆，以現有筆數平均（與 ATR-20d 行為一致）。
- 高價 Spread% 目前只記錄於 summary，尚未作為 `downgrade_reason` 傳入 CandidateInput；如需進入排名邏輯，需後續 sprint 補充。

### Sprint 3-B: Ops-run 前置安全驗證

目前狀態：已完成。commit `455be7e`

- **Git clean check** (`check_git_clean`)：執行 `git status --porcelain` 與 `git diff --check`，回傳 `{clean, uncommitted_files, whitespace_issues}`。dirty repo 時在 manifest 的 `blocked_reasons` 加入 `uncommitted_changes`，但不中斷執行（警告+記錄）。
- **Lock file 偵測** (`check_ops_lock`)：偵測 `.ops.lock` 是否存在，存在時加入 `ops_lock_file_exists` 到 `blocked_reasons`；ops-run 開始時建立 lock，結束時清除（盡力清除，不拋例外）。
- **Manifest `git_status` 欄位**：manifest dict 新增 `git_status` 鍵，記錄 git 狀態摘要，確保每日 run 可從 manifest 獨立稽核環境狀態。
- 新增 `tests/test_ops_run.py`，8 個測試，全數通過，總計 120 tests。

殘餘風險：
- SIGKILL 後 `.ops.lock` 殘留，需手動清除（intentional，可稽核）。
- 兩個 pre-flight check 均為 warning-only，不硬阻擋；若未來需強制 clean repo 才能送單，可升級為 hard abort。

### Sprint 3-C: 大盤環境過濾器（Market Regime Filter）

目前狀態：已完成。commit `c45579b`

- 新增 `src/tw_day_trading_lab/market_regime.py`，純函數模組：
  - `compute_market_regime(price_rows, *, min_atr5d_pct=0.5, gap_down_threshold_pct=-1.5)`
  - EMA20：前 20 根收盤均值為種子，k = 2/(20+1) 指數平滑
  - 5日 ATR%：最近 5 根 true range 均值 / 收盤價 × 100
  - gap_open_pct：今日開盤 vs 前日收盤漲跌幅
  - 判斷邏輯：`bearish_skip`（任一條件成立）/ `bullish`（全清）/ `neutral`（其他）
  - < 21 筆資料時回傳 `neutral`，reasons 含 `insufficient_history`
- 使用 **0050 元大台灣50 ETF** 作為大盤環境 proxy（FinMind TaiwanStockTotalReturnIndex API 在目前 token 下不可用）
- `build_candidates_from_raw_cache` 整合：ranking 後載入 0050 proxy，regime 結果記錄於 `summary['market_regime']`；`bearish_skip` 時將 `next_day_actionable=True` 的候選全部標為 `next_day_actionable=False` 並附加 `market_regime_blocked` downgrade reason。
- 新增 `tests/test_market_regime.py`（7 個測試）與 `test_candidate_builder.py` 補 2 個 Sprint 3-C 測試，全數通過，總計 129 tests。

殘餘風險：
- 0050 proxy 需在 ingestion 時一起抓取（需在 finmind request list 加入 `stock_id='0050'`）；若 cache 無 0050，系統 fallback 為 `neutral`（不阻擋）。
- EMA20 在剛好 21 筆時僅 1 筆參與指數平滑，訊號不穩定；操作者應確保 cache 有足夠歷史窗口（建議 ≥ 30 筆）。
- `CandidateScore.downgrade_reasons` 在 bearish 修補後為 list（原始為 tuple），若下游需要 tuple 型別，後續 sprint 可補 `tuple(...)` wrap。

### Sprint 3-C 剩餘: 0050 Ingestion

目前狀態：已完成。

- 在 `examples/finmind.requests.sample.json` 中加入 0050 的 `TaiwanStockPrice` request，且指定 `start_date` 為 `2026-03-01`，確保為 EMA20 預留足夠大於 21 個交易日的歷史數據。
- 執行 nightly ingestion，成功將 0050 的大盤歷史價格下載並 cache 於 `data/raw/finmind/TaiwanStockPrice/2026-06-03/0050.jsonl`，不再 fallback `neutral`。
- 重新建立 candidates，2026-06-03 大盤的 `market_regime` 成功識別為 `bullish`。

### Sprint 4-A: Telegram 正式發送 Gate

目前狀態：已完成。

- **`TELEGRAM_ENABLED` 環境變數大開關**：在 `cmd_notify_telegram` 與新引入的 `send_alerts_telegram` 中支援 `TELEGRAM_ENABLED` 環境變數檢測。若為 `false` 或者是 `missing`，即使 API Token 和 Chat ID 設定，也不會實際發送（列印 dry-run 或 skip）。
- **`--dry-run` 優先權最高**：即使 `TELEGRAM_ENABLED=true`，只要用戶指定 `--dry-run` 或 dry_run 參數為 `True`，也只列印不發送。
- **Severity 與 Deduplication 過濾閘門**：
  - 只有 `severity` 為 `"error"` 或 `"critical"` 的 Alert 才會嘗試發送。
  - 同一 `dedupe_key` 的 alert 預設在 24 小時內不重複發送（時間差 `< 86400` 秒）。發送記錄儲存在 `data/telegram_send_log.json` 中。
- **Ops Run 串接**：在 `cmd_simulate_ops_run` 尾部正式引入 `send_alerts_telegram` 函數呼叫，生成 manifests 之前完成警報過濾與發送。
- 新增 `tests/test_simulation.py` 中的 `test_send_alerts_telegram` 覆蓋 24 小時 dedupe、環境變數關閉、severity 過濾。

### Sprint 4-B: Live Approval Token HMAC 硬化

目前狀態：已完成。

- **HMAC-SHA256 帶時效驗證**：
  - 在 `live.py` 中重構 `LiveShioajiBrokerAdapter.check_live_execution_gate`，捨棄原本可預測的 `{date}:LIVE-TRADING-APPROVED` 格式，改為使用 `LIVE_APPROVAL_SECRET` 作為 key，對 UNIX timestamp 進行 HMAC-SHA256 簽章，格式為 `{timestamp_unix}:{hmac_hex}`。
  - 驗證端解析 `timestamp_unix`，若其與系統當前時間（`time.time()`）之差絕對值大於 300 秒（5 分鐘），則回傳 `manual_approval_token_expired` 阻擋。
  - 若 `LIVE_APPROVAL_SECRET` 在環境變數中未設定，回傳 `live_approval_secret_missing` 阻擋。
- **向下相容與 Offline 政策驗證**：
  - 在 `build_production_readiness_report` 整合 HMAC 驗證。若 `LIVE_APPROVAL_SECRET` 未設定，fallback 回與 `policy.expected_manual_approval_token` 做一般字串比對，以確保不破壞離線測試與沒有 live-trading 密鑰時的開發。
  - `LiveShioajiBrokerAdapter` 如果沒有 `LIVE_APPROVAL_SECRET` 且有設定 `expected_token_hash`，亦會 fallback 回舊 hash 比對。
  - 在 `cli.py` 的 `cmd_simulate_production_readiness` 移除原本 `{args.date}:LIVE-TRADING-APPROVED` 的可預測 fallback 預設值，改為必須明確指定。
- **新增 CLI 生成命令**：
  - 新增子命令 `tw-daytrade simulate generate-approval-token`，讀取當前環境的 `LIVE_APPROVAL_SECRET` 並基於當前系統時間產生一個 5 分鐘內有效之驗證 token，方便維運人員操作。
- 新增 `tests/test_live_approval_hmac.py`，完整覆蓋 token 生效、過期、無 secret 拒絕等場景。

### Sprint 4-C: TiDB Migration Strategy

目前狀態：已完成。

- **Migration 版本追蹤表**：
  - 新增 `sql/002_schema_version.sql` 建立 `schema_migrations` 表：`version INT PRIMARY KEY`, `applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, `description TEXT`。
- **Database 遷移管理層**：
  - 在 `storage.py` 新增 `get_schema_version(connection) -> int` 讀取當前最高版本號（若表不存在則回傳 `0`）。
  - 新增 `apply_migration(connection, version, sql, description) -> None`，支援 transaction 與 rollback，並在套用後寫入 `schema_migrations` 以防止重複執行。
  - 支援 SQLite 測試庫與 TiDB (MySQL) 語法相容，自動使用 `?` (SQLite) 或 `%s` (MySQL) 預留位置。
- **Migration CLI 工具**：
  - 新增 `tw-daytrade db migrate --schema-dir sql` 子命令。會自動掃描 `sql/` 下以 `(\d+)_(.*)\.sql` 為命名格式的檔案，按照版本號排序，只執行尚未套用的遷移。
  - 在執行 SQL 遷移前，會自動先以 `use_database=False` 連線並執行 `CREATE DATABASE IF NOT EXISTS {db}; USE {db}`，防止新環境下 database 不存在而連線失敗。若遇到不支援 `CREATE DATABASE` 語句之連線（如 SQLite 記憶體庫），會優雅捕捉例外並忽略，以維持最佳適應力。
- 新增 `tests/test_db_migration.py`，驗證 migration 套用、不重複套用與 db migrate 命令流程。

### 全數測試與 Whitespace 驗收

- 總測試案例增加至 **138 tests**，執行 `PYTHONPATH=src python3 -m unittest discover -s tests` 與 `compileall`，全數無警告、OK 通過。
- 執行 `git diff --check` 校正 trailing whitespace，結果完全乾淨。

## 13. Documentation Baseline Sync - 2026-06-04

本輪目標：修正 `README.md` 與 `AGENTS.md` 仍停留在「下一步 P8」的舊狀態，避免後續開發被錯誤導回已完成 phase。

### 更新內容

- `README.md`：
  - 將 Current Implementation Status 更新為 P0-P11、Phase B、Sprint 3-A/B/C、Sprint 4-A/B/C 皆已完成。
  - 補上 `.pfx` / `.p12` ignore 與 `Sinopac.pfx` history cleanup 已完成。
  - 將 Remaining Phase Map 改為 Completed Phase Map。
  - 將下一步改為 `Daily Simulation Ops Automation v1`。
  - 補上 `db migrate`、`simulate ops-run`、`simulate regression-import` 等目前實際可用命令。
- `AGENTS.md`：
  - 將 agent 的 Current Status 更新到 P8-P11 / Phase B / Sprint 4-C 後狀態。
  - 明確警告不要把 P8 當作未完成重新實作。
  - 將下一輪 priority 改為 one-command daily ops runner + daily bundle audit + regression import。

### Next-run Seed

下一輪 coding 建議做 `Daily Simulation Ops Automation v1`：

1. 新增 `make daily-ops DATE=YYYY-MM-DD` 或等價 runner。
2. 串接 FinMind ingestion、0050 market regime input、candidate build、`simulate ops-run`、close report / alerts、`simulate regression-import`。
3. 產出單一 daily bundle，讓 manifest、readiness、alerts、report、regression cases 可由同一 run id 追蹤。
4. 保持正式 live order blocked；`simulation-on` 與真實 Shioaji side effect 仍需明確 gate。

### Residual Risks

- README / AGENTS 已同步，但 `MEMORY.md` 仍有部分長期摘要停在較舊的 phase 認知，後續若需要可另開 memory promotion / cleanup。
- Daily ops runner 尚未實作；目前只是文件基線修正。

## 14. Daily Simulation Ops Automation v1 - Steps 1-2 - 2026-06-04

本輪目標：照文件 next-run seed 往下實作兩步，先完成 one-command daily ops runner / Make target，再完成 daily bundle audit。仍不啟用正式下單。

### Step 1: Daily Runner / Make Target

已新增 `simulate daily-ops` 子命令，作為每日營運鏈上層 orchestration：

1. 選定 `trading_date` 與輸出目錄，預設為 `reports/{date}-daily-ops/`。
2. 執行 FinMind ingestion；未提供 `--requests` 時會依當日自動產生 market / stock info / 0050 proxy requests，本地 fixture 或無 token 驗證可用 `--skip-ingestion`。
3. 從 raw cache 建立 `candidates.json`，並保留 market regime summary。
4. 呼叫既有 `simulate ops-run`，產出 `ops/ops_run_manifest.json`、`alerts.json`、`readiness_report.json`、`simulation_output.json` 等 artifact。
5. 呼叫 `report close` 產出 `close.md` 與 `telegram-summary.txt`。
6. 呼叫 `simulate regression-import`，將 alerts 轉成可追蹤 regression cases。

安全邊界：`simulate daily-ops` 預設將 alert 發送視為 dry-run，且預設忽略 `IS_SIMULATION` 環境變數以避免環境殘留觸發 Shioaji side effect；只有明確帶 `--send-alerts` 或 `--simulation-on` 才會允許對應外部動作。

同步新增 Make target：

```bash
make daily-ops DATE=2026-06-04
make daily-ops DATE=2026-06-04 ARGS="--skip-ingestion --allow-outside-session --fail-on-audit"
make daily-ops DATE=2026-06-04 ARGS="--send-alerts"
```

### Step 2: Daily Bundle Audit

已新增 `audit_daily_ops_bundle`，並在 `simulate daily-ops` 尾端產生 `daily_bundle_audit.json`。

Audit 檢查項目：

- manifest 是否存在。
- run id / trading date 是否存在。
- input / output artifact 是否存在。
- artifact checksum 是否與 manifest 相符。
- 必要 artifact 是否齊全：`input_plan.json`、`simulation_output.json`、`restart_sync.json`、`readiness_report.json`、`alerts.json`、`callback_store.json`。
- readiness report 是否含 status / summary。
- alerts 是否為 list，且每筆 alert 都有 severity、category、owner、manual_action、send_gate、dedupe_key。
- close report 是否存在。
- regression cases 是否可追到同一 run id。

### 驗證

- 新增 `tests/test_daily_ops.py`：
  - `test_daily_ops_builds_auditable_bundle`
  - `test_bundle_audit_detects_missing_artifact`
- 本輪 smoke 使用 `--skip-ingestion --allow-outside-session --fail-on-audit`，確認每日鏈可在無 FinMind token / 無正式下單下產出 auditable bundle。

### Next-run Seed

下一輪不應直接推正式下單。建議做「3-5 日穩定性觀察」：

1. 對 3-5 個交易日或 fixture 日期跑 `make daily-ops DATE=YYYY-MM-DD`。
2. 彙總每次 `daily_bundle_audit.json`、`ops/alerts.json`、`ops/readiness_report.json`。
3. 找出重複 blocker、alert noise、candidate quality、partial fill、ordering issue。
4. 只針對重複且會阻礙操作者的問題開小 sprint 修正。
5. 若要開啟真實 Telegram alert 或 Shioaji simulation side effect，另開 gate review，不要在一般 smoke 裡使用 `--send-alerts` 或 `--simulation-on`。

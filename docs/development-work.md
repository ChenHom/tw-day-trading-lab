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

## 15. Direction Alignment Review - Trading Day Autonomous Cycle - 2026-06-04

本輪目標：依照使用者重新確認的真實目標，審查目前架構與文件是否偏離方向，並直接修正文件。

### 使用者重新確認的 Objective

系統目標不是只跑一次候選 / simulation bundle，而是每個交易日自動循環：

1. 使用既有候選股名單作為當沖監控範圍。
2. 09:05 或 10:00 開始盤中監控。
3. 09:05/10:00-13:20 期間反覆檢查候選股是否符合進場 / 出場策略，通過風控後執行。
4. 13:20 停止新進場，執行 time stop / force-exit / cancel policy。
5. 收盤後等待資料沉澱。
6. 15:00 彙整當日交易結果、優缺點、blockers 與 next actions，發布到 GitHub 並回傳連結。
7. 17:30 整理與產出下一交易日候選名單。
8. 每個交易日自動重複。

### Verdict

目前架構「部分正確，但下一步文件方向偏窄」。

已走在正確方向的基礎：

- candidate engine / candidate builder 已能產生候選名單。
- `VwapBreakoutStrategy`、風控、成本、exit checks 已有第一版策略與風險零件。
- P6-P11 已把 Shioaji simulation、callback sync、readiness、alerts、regression、live boundary 拆開。
- `simulate daily-ops` 已能產生 auditable daily bundle。

偏掉的地方：

- README / AGENTS 把下一步寫成「3-5 日 daily-ops 穩定性觀察」，容易讓後續工作只停在 bundle 稽核。
- `simulate daily-ops` 是單次 orchestration，不是 09:05/10:00-13:20 的 time-aware intraday watch loop。
- 文件缺少 trading-day scheduler / run state / stage transition 契約。
- 文件缺少 15:00 GitHub publish + operator link 契約。
- 文件缺少 17:30 next-candidate handoff 契約。

### 文件修正

- 新增 `docs/trading-day-autonomous-cycle.md`：
  - 明確定義交易日自動循環 objective。
  - 寫入 17:30 / 09:05 or 10:00 / 13:20 / 15:00 / 17:30 schedule。
  - 記錄目前架構 gap。
  - 定義 Phase C: Trading Day Autonomous Cycle v1 的 C1-C5 next implementation seed。
- 更新 `README.md`：
  - 新增 Target Operating Cycle。
  - 將下一步從 daily-ops 穩定觀察改成 `Trading Day Autonomous Cycle v1`。
  - 保留正式 live order blocked 的安全邊界。
- 更新 `AGENTS.md`：
  - 明確指示後續 agent 不要把 `daily-ops` 當作完整產品。
  - 下一輪 priority 改為 `simulate trading-day-cycle` / run state / intraday watch loop / 15:00 GitHub report / 17:30 candidates。
- 更新 `docs/mvp-roadmap.md`：
  - 新增 Phase C: Trading Day Autonomous Cycle。
  - 拆出 C1 scheduler、C2 watch loop、C3 execution / force-exit、C4 15:00 report publish、C5 17:30 next candidates。
- 更新 `docs/data-contracts.md`：
  - 新增 trading-day run state contract。
  - 新增 intraday watch event contract。
  - 新增 15:00 report publish contract。

### Corrected Next-run Seed

下一輪 coding 不應直接正式下單，也不應只跑 3-5 日 `daily-ops`。

建議下一輪做 `Trading Day Autonomous Cycle v1` 第一刀：

1. 新增 dry-run `simulate trading-day-cycle` 或等價 runner。
2. 產出 `trading_day_run_state.json`，包含 stage transition、schedule policy、artifacts、blocked reasons、manual actions。
3. 用 fixture 驗證 09:05/10:00 start、13:20 hard stop、15:00 report stage、17:30 next candidates stage。
4. 建立 intraday watch loop 的最小 fixture contract：approved entry、rejected entry、exit trigger、no-action candidate。
5. 所有 live execution、Telegram real send、Shioaji simulation side effects 繼續需要明確 gate。

### Residual Risks

- 這一輪只修正方向文件，尚未實作 `simulate trading-day-cycle`。
- 目前 `ops-run` 裡有 strategy trigger 雛形，但它不是長時間 watch loop；後續不能誤認為 intraday loop 已完成。
- GitHub report publish 需要另外確認 artifact 位置、private repo link 可見性與發送 gate。
- 17:30 next-candidate builder 需要交易日 calendar / holiday handling，不能只用當天日期字串。

## 16. Trading Day Autonomous Cycle v1 - Steps 1-2 - 2026-06-04

本輪目標：依使用者指示開始做前兩步：

1. `simulate trading-day-cycle` dry-run runner + state machine。
2. Trading-day clock policy / data availability policy。

使用者修正：交易日不用額外 holiday calendar 判斷；只要 API / raw cache 取不到交易資料，就判定為非交易日。

### Step 1: Dry-run Runner / State Machine

已新增 `simulate trading-day-cycle` 子命令。

輸出：

```text
reports/{date}-trading-day-cycle/trading_day_run_state.json
```

目前 state 會記錄：

- `trading_day_run_id`
- `calendar_status`
- `calendar_rule=api_data_availability_only`
- `start_policy`：`09:05` 或 `10:00`
- `hard_stop_time=13:20`
- `close_buffer_end_time=14:00`
- `report_time=15:00`
- `next_candidate_time=17:30`
- `stage`
- `stage_history`
- `candidate_artifact`
- `trading_data_probe`
- `position_state_artifact`
- `report_artifact`
- `next_candidate_artifact`
- `idempotency_key`
- `lock`
- `retry_policy`
- `blocked_reasons`
- `manual_actions`
- `side_effects=[]`

指令範例：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle \
  --date 2026-06-04 \
  --trading-data-input data/raw/finmind/TaiwanStockPrice/2026-06-04/0050.jsonl \
  --candidates-input reports/2026-06-04-candidates.json \
  --start-policy 09:05 \
  --run-all-stages
```

### Step 2: Data Availability / Clock Policy

已新增資料探針：

- 優先讀 `--trading-data-input`。
- 未指定時，依序讀：
  - `data/raw/finmind/TaiwanStockPrice/{date}/{market_proxy_stock_id}.jsonl`
  - `data/raw/finmind/TaiwanStockPrice/{date}/market.jsonl`
- 任一來源有 rows：`calendar_status=trading_day`。
- 完全無 rows：`calendar_status=non_trading_day`、`stage=blocked`、`blocked_reasons=["trading_data_unavailable"]`。

已新增 clock stage resolver：

- current < start policy：`intraday_waiting`
- start policy <= current < 13:20：`intraday_running`
- 13:20 <= current < 14:00：`force_exit`
- 14:00 <= current < 15:00：`close_buffer`
- 15:00 <= current < 17:30：`reporting`
- current >= 17:30：`next_candidates`

### Tests

新增 `tests/test_trading_day_cycle.py`：

- `test_trading_day_cycle_full_dry_run_uses_api_data_availability`
- `test_trading_day_cycle_no_api_rows_marks_non_trading_day`
- `test_clock_policy_resolves_intraday_and_reporting_stages`

### Next-run Seed

下一輪做第 3-4 步：

1. 新增 intraday candidate watch loop fixture：只監控候選名單，不掃全市場。
2. 每次候選檢查輸出 watch event：approved entry、rejected entry、exit trigger、no-action candidate。
3. 將 entry / exit strategy loop 接上 position state；open position exit check 優先於 new entry。
4. 保持正式 live order blocked；Shioaji simulation side effect 仍必須明確 `--simulation-on` gate。

## 2026-06-04 Trading Day Cycle Steps 3-4

已新增 fixture / dry-run intraday watch loop，接在 `simulate trading-day-cycle`。

新增 CLI inputs：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle \
  --date 2026-06-04 \
  --trading-data-input data/raw/finmind/TaiwanStockPrice/2026-06-04/0050.jsonl \
  --candidates-input reports/2026-06-04-candidates.json \
  --intraday-bars-input fixtures/2026-06-04-intraday-bars.json \
  --position-state-input reports/2026-06-04-trading-day-cycle/position_state.json \
  --current-time 09:20
```

新增 artifact：

```text
reports/{date}-trading-day-cycle/watch_events.json
```

目前行為：

- 只載入 `--candidates-input` 內的候選股，不盤中掃全市場。
- 無持倉候選用 `VwapBreakoutStrategy` 做 entry dry-run。
- 有 open position 的標的先做 exit dry-run，不同輪產生新的 entry intent。
- 13:20 後的新 entry 一律 `entry_rejected` / `after_hard_stop`。
- `trading_day_run_state.json` 會回填 `watch_events_artifact` 的 path / checksum / summary。
- 本輪仍為 dry-run / fixture；`side_effects=[]`，不登入券商、不送單、不發通知。

新增 / 更新測試：

- `test_intraday_watch_loop_emits_entry_and_no_action_for_candidates_only`
- `test_open_position_exit_is_evaluated_before_new_entry`
- `test_after_hard_stop_rejects_new_entry`

Next-run seed：

1. 將 `watch_events.json` 的 approved entry / approved exit 轉成 dry-run order intents。
2. 補 execution policy：duplicate intent、pending order、partial fill、callback ordering、max position、daily risk。
3. 補 13:20 force-exit / stale pending cancel policy。
4. 正式 live order 繼續 blocked；Shioaji simulation side effect 仍需明確 gate。

## 2026-06-04 Trading Day Cycle Steps 5-8

已將 `simulate trading-day-cycle` 從 watch-loop fixture 擴成完整 dry-run bundle。

新增 artifacts：

```text
reports/{date}-trading-day-cycle/order_intents.json
reports/{date}-trading-day-cycle/position_state.json
reports/{date}-trading-day-cycle/report.md
reports/{date}-trading-day-cycle/next_candidates.json
reports/{date}-trading-day-cycle/end_to_end_smoke.json
```

目前行為：

- `watch_events.json` 的 approved entry / exit 會轉成 `order_intents.json`。
- duplicate intent 會標為 `duplicate_suppressed`。
- max open positions / daily risk stop 可 block 新 entry。
- 13:20 後 stale pending order 會產生 dry-run cancel intent。
- open position 在 hard stop 後可產生 time-exit dry-run sell intent。
- partial fill lifecycle decision 只列為 manual action，不自動改寫 position。
- `position_state.json` 不做 fill mutation，只保存 source state、generated order / cancel intents 與 manual actions。
- `report.md` 是 15:00 local / would-send dry-run report，`publish_status=dry_run`、`send_status=dry_run_not_sent`。
- `next_candidates.json` 是 17:30 handoff；不查額外交易日曆，不猜日期，使用 `next_api_available_trading_day` policy。
- `end_to_end_smoke.json` 檢查 candidate / watch / order intents / position / report / next-candidate artifact 是否完整。
- 本輪仍為 dry-run / fixture；`side_effects=[]`，不登入券商、不送單、不發通知。

新增測試：

- `test_full_cycle_outputs_order_report_next_candidates_and_smoke`
- `test_execution_policy_suppresses_duplicate_and_blocks_max_positions`
- `test_force_exit_creates_exit_and_cancel_intents_after_hard_stop`

Next-run seed：

1. 跑 3-5 個 fixture / simulation-side-effect gated smoke days，觀察 bundle 穩定性。
2. 接 API-backed intraday market-data adapter，但保持 `watch_events.json` contract 不變。
3. 另開 gate 實作真實 GitHub publish / Telegram operator link send。
4. 正式 live order 繼續 blocked；不得把 smoke OK 解讀成策略有 edge。

## 2026-06-04 Simulate Online-Test Preparation MVP

已把完整 dry-run trading-day cycle 往 simulate 上線測前的穩定作業鏈推進。

新增 / 更新 CLI：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle \
  --date 2026-06-04 \
  --candidates-input reports/2026-06-04-candidates.json \
  --current-time 09:20

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle-smoke \
  --dates 2026-06-04,2026-06-05,2026-06-06 \
  --cache-dir data/raw \
  --candidates-input-pattern 'reports/{date}-candidates.json' \
  --run-all-stages
```

目前行為：

- `simulate trading-day-cycle` 若未提供 `--intraday-bars-input`，會依候選股 symbol 從 raw cache 讀取 `data/raw/finmind/TaiwanStockPriceMinute/{date}/{symbol}.jsonl`。
- raw-cache adapter 仍是 candidate-scoped，不盤中掃全市場，並維持 `watch_events.json` contract 不變。
- `trading_day_run_state.json` 新增 `intraday_data_adapter`，記錄 mode、dataset、candidate-scoped source paths 與 rows。
- 新增 `simulate trading-day-cycle-smoke`，可連跑多個日期並輸出 `multi_day_smoke_summary.json` / `multi_day_smoke.md`。
- multi-day smoke 對有 API/raw-cache trading rows 的日期檢查完整 artifact chain；取不到 trading rows 的日期標為 `skipped_non_trading_day`，不查額外假日日曆。
- Shioaji simulation side effects、GitHub publish、Telegram send 仍維持 disabled / dry-run gate。

新增測試：

- `test_intraday_watch_loop_can_use_candidate_scoped_raw_cache`
- `test_trading_day_cycle_smoke_summarizes_ok_and_non_trading_days`

Next-run seed：

1. 實際跑 3-5 個 raw-cache / fixture-backed smoke days，收集 stability summary。
2. 另開 explicit gate 接 Shioaji simulation side effects；未開 gate 時必須保持 disabled。
3. 另開 explicit gate 接 GitHub publish / Telegram operator link send；未開 gate 時必須保持 dry-run only。
4. 正式 live order 繼續 blocked；smoke OK 不得解讀成策略 edge。

## 2026-08-14 ~ 2026-08-16 Price Action Intraday PA-P1 / PA-P2 / PA-P3

### 命名衝突警告

`docs/price-action-intraday-plan.md` 的 P1-P9 與本 roadmap 的 P0-P11 是**兩套完全不同的編號**。

| 編號 | 本 roadmap | Price Action plan |
|---|---|---|
| P1 | Old Log / CSV Importer | Shioaji Tick → MarketTick |
| P2 | TiDB Integration | Tick → 1m Aggregator |
| P3 | FinMind Nightly Ingestion | 1m → 5m Aggregator |

文件與 commit 一律用 `PA-P1` / `Price Action Intraday P1` 這種前綴指涉後者，避免誤讀。

### PA-P1: Shioaji Tick → normalized MarketTick

新增 `src/tw_day_trading_lab/market_data.py`、`tests/test_market_data.py`，CLI 新增 gated `simulate shioaji-tick-smoke`。

設計文件：`docs/price-action-p1-design.md`。

四個在 contract 層定案的決策：

- **D1 sequence 自建**。Shioaji `TickSTKv1` 沒有 sequence 欄位（已對 SDK 1.3.2 原始碼查證）。改由 stream 指派 per-`(date, symbol)` 遞增計數器。重啟歸零，replay 的真正排序 / 去重鍵是 `(timestamp, cumulative_volume)`。
- **D2 `simtrade` 必須擋掉**。試撮 tick 從 08:30 開始流，不濾會讓 1m aggregator 生出 08:30-09:00 幽靈 K 棒且 `open` 錯誤。
- **D3 `intraday_odd` 必須擋掉**。SDK docstring 明載整股 `volume` 單位是 K shares（張），盤中零股是 share（股），混入會差 1000 倍。
- **D4 canonical volume 單位 = 股**。整股 tick 乘 1000。**FinMind `TaiwanStockPriceMinute` 的單位仍未查證**，見 Residual Risks。

Code review 第一輪修正（六項資料正確性 / 串流問題）：

1. provider callback 內同步寫 JSONL → 改為只 `put_nowait` 進 bounded queue，raw 寫入 / normalize / dedupe / sink 全移到 worker thread。卡住 quote callback 會讓整條 tick feed 延遲，所有 volume 衍生特徵靜默失真。
2. `volume` 缺失或無法解析被靜默轉成 0 → 納入 required fields，缺失 / 無法解析 / 負值一律 `needs_review`。`volume=0` 仍是合法值。
3. candidate scope 只在 subscribe 端成立 → ingestion 端再檢查一次。同一 api 物件的 quote callback 是共用的，subscribe 範圍不等於 ingestion 範圍。
4. duplicate tick 會重複進 sink 灌大成交量 → 以 `(symbol, timestamp, cumulative_volume)` 去重。
5. `code` 缺失的 tick 不寫 raw → 改寫入 `_unknown.jsonl`，那正是最需要事後追查的一種。
6. 例外時可能沒 unsubscribe → `start()` partial rollback、`stop()` 先 unsubscribe、gate `try/finally`、CLI `finally` logout。Shioaji 對同一 `person_id` 有連線數上限。

Code review 第二輪修正（market data health）：

原本 `_run_worker` 直接呼叫 `_process` 無 try/except，worker 一掛就靜默死亡，之後每筆 tick 全部消失且無跡可循。現在每種遺失路徑都有獨立 counter，聚合成 `HEALTHY` / `DEGRADED` / `FAILED`：

| 遺失路徑 | counter | health |
|---|---|---|
| queue 滿 | `dropped_queue_full` | DEGRADED |
| 單筆處理例外 | `worker_errors`（worker 存活） | DEGRADED |
| raw 寫入失敗 | `raw_write_errors`（tick 仍送下游） | DEGRADED |
| sink raise | `sink_errors` | DEGRADED |
| worker loop 死亡 | `worker_failed` | FAILED |
| worker join 逾時 | `worker_stop_timeout` | FAILED |

並修正 concurrency contract：`stop()` 若 join 逾時就**不 drain**，直接標 FAILED；`drain()` 在 worker 存活時 raise。原本「join timeout 後主執行緒照樣 drain」會讓兩條 thread 同時跑 `_process`，破壞 sequence / dedupe / volume 狀態。

### PA-P2: MarketTick → canonical 1m MarketBar

新增 `src/tw_day_trading_lab/bars.py`、`tests/test_bars.py`，CLI 新增 `bars build-1m`。

設計文件：`docs/price-action-p2-design.md`。

**決策反轉：Missing Minute Policy 不補 synthetic bar。**

`docs/price-action-intraday-plan.md` 原本要求缺分鐘用 previous close 補空棒並標 `is_synthetic=true`。實作時改為不補，原因是這三件事必須保持可分辨：

| 事實 | 表現 |
|---|---|
| 真的成交量 0（有成交，量為 0） | 有 bar，`volume=0`、`trade_count>0` |
| 根本沒有交易 | 沒有 bar，`no_trade_minutes` 計數 |
| feed 漏資料 | `market_data` health 轉 DEGRADED / FAILED |

補空棒會把前兩者混成同一種 bar，第三者則被偽裝成「市場很安靜」。要不要補空棒交給下游 normalization layer 依用途決定。`MarketBar` 因此沒有 `is_synthetic` 欄位。原始計畫文件已標註此修正並保留原文對照。

其他關鍵決策：

- **事件時間，不讀系統時鐘**。watermark 只由 tick timestamp 推進，這是「live 與 replay 共用同一套 aggregator」的必要條件。需要在無成交時收 bar 的呼叫端用 `flush(now=...)` 自己給時鐘。
- **late tick window 導致同一 symbol 會有多個 pending bar**（09:00 等遲到、09:01 進行中），因此內部不是單一 `current_bar` 而是 `pending[symbol][minute]`。窗內遲到直接併入不產生 correction，窗外才 `CORRECTED rev=2`。
- `MarketBar` 是 frozen dataclass，correction 產生新物件，`bar_revision_used` 天然成立，不需額外機制。
- open / close 依**事件時間**決定，不依抵達順序，否則遲到 tick 會僅因為最後抵達就變成 close。

### PA-P3: canonical 1m → canonical 5m

同一個 `bars.py` 新增 `FiveMinuteBarAggregator`、`aggregate_1m_to_5m`；新增 `tests/test_bars_5m.py`。

設計文件：`docs/price-action-p3-design.md`。

三個必須成立的性質：

1. **bucket 是時間區間，不是「五根 1m」**。PA-P2 對無成交分鐘不發 bar，所以一根 5m 可能只由 4 根或 1 根組成。不補假資料、不因不足五根就不產生、不借下一個 bucket 湊數。
2. **1m correction 是 replace 不是 accumulate**。component 存的是 `minute → 最新 revision 的 1m bar`，每次變更重算整根，結構上不可能出現 `1000 + 1500 = 2500`。這是唯一會讓輸出「不再 canonical」的環節。
3. **已發出的 revision 不可變**。

順手修的既有 bug：`latest_bars()` 原本 key 是 `(symbol, start_at)`，1m 與 5m 的同一時間點會互相覆蓋，已改為含 `timeframe`。PA-P2 當時只有一種 timeframe 所以沒暴露。

刻意不做（不影響 PA-P3 成立）：`component_bar_count`、`missing_minutes` 欄位、`bars build-5m` CLI、5m 專屬 metrics。

### 兩段驗收模型

三個 phase 都拆成兩個 gate，避免非交易時段卡住開發，也避免把「unit test 全綠」誤讀成「真實行情已驗證」：

```text
PA-P1_CODE_COMPLETE ✅    PA-P1_LIVE_VALIDATED ❌
PA-P2_CODE_COMPLETE ✅    PA-P2_LIVE_VALIDATED ❌
PA-P3_CODE_COMPLETE ✅    PA-P3_LIVE_VALIDATED ❌
```

`simulate shioaji-tick-smoke` 的 report 內含 `live_validation` 區塊，自行計算是否達到 live 標準，不需人工核對欄位。**`status` 與 `live_validation` 是兩個不同問題**：非交易時段跑，pipeline 可以完全健康（`status=ok`、`health=HEALTHY`）但一筆 tick 都沒收到，此時 `live_validation.passed=false`。只有 `passed=true` 能關閉 PA-P1。

### 可調參數（刻意留下的上限，皆有 `ponytail:` 註解）

| 常數 | 預設 | 上限說明 |
|---|---|---|
| `TICK_QUEUE_MAXSIZE` | 100000 | 滿了丟棄並計入 `dropped_queue_full` |
| `DEDUPE_WINDOW` | 512 | 有界近期窗口而非整場 set；整場 set 語意更純但 50 檔 × 20k ticks 會長到數百 MB |
| `DEFAULT_LATENESS_SECONDS` | 3 | 1m bar finalize 前的等待窗 |
| `CORRECTION_WINDOW_MINUTES` | 10 | 1m 可被 correct 的保留期 |
| `DEFAULT_5M_CORRECTION_WINDOW_MINUTES` | 30 | 5m bucket 可被 correct 的保留期 |

### 驗證

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # Ran 248 tests, OK
PYTHONPATH=src python3 -m compileall -q src tests
git diff --check
```

測試數變化：154 → 175（PA-P1）→ 187 → 198 → 204（review 修正 + health + live gate）→ 228（PA-P2）→ 248（PA-P3）。

邊界檢查：

```bash
grep 'import shioaji' src/tw_day_trading_lab/market_data.py   # 無
grep '^from \|^import ' src/tw_day_trading_lab/bars.py        # stdlib + MarketTick 而已
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-tick-smoke \
  --date 2026-08-14 --symbols 2330,2317                       # blocked / side_effects=[] / exit 0
```

### Residual Risks

1. **三個 LIVE_VALIDATED 全部未達成**，都等同一次交易時段實跑。unit test 全綠不等於真實行情已驗證。
2. **D4 未查證**：FinMind `TaiwanStockPriceMinute` 的 volume 單位（張或股）沒驗過，本機 `data/raw/finmind/` 連一天分 K 都沒有。PA-P2 Gate B 要拿它當比對基準，比對前必須先解決，否則會誤判成 ×1000 錯誤。驗法：抓一天分 K，把當日總量對 `TaiwanStockPrice.Trading_Volume`。
3. **同一問題也影響既有 trading-day cycle**：`--require-intraday-bars` 目前對任何日期都會 `blocked`，`fixtures/` 目錄不存在（`README.md` 有引用）。
4. **SDK 版本相容性只有 fake 覆蓋**：本機是 shioaji 1.3.2，`sj.Shioaji` 沒有 top-level `subscribe` / `set_on_tick_stk_v1_callback`（實測），只有 `api.quote.*`。實作同時支援兩種形狀且各有測試，但真實 API compatibility 只能由實跑確認。
5. **Shioaji simulation 帳號能否收到即時 tick**：review 引用官方 Simulation Mode 文件指出 `quote.subscribe` / `ticks` / `kbars` / `snapshots` 可用（本專案未自行查證）。待實跑確認本帳號 + 1.3.2 + 本實作確實收得到。
6. `sequence` 重啟歸零，跨 process 的 replay 排序仍須靠 `(timestamp, cumulative_volume)`。

### Next-run Seed

1. 交易時段跑 `simulate shioaji-tick-smoke --symbols 2330 --duration-seconds 60 --enable-tick-stream`，檢查 `live_validation.passed`。通過即 `PA-P1_LIVE_VALIDATED`。
2. 抽查 `data/raw/shioaji/ticks/{date}/2330.jsonl`，確認 `datetime` / `close` / `volume` / `total_volume` 與 callback 收到的一致。
3. 先解決 Residual Risk 2（FinMind 分 K 單位），再跑 `bars build-1m` 並與 `TaiwanStockPriceMinute` 比對 OHLC / volume / bar count / missing minute，通過即 `PA-P2_LIVE_VALIDATED`。
4. 5m 比對同上，通過即 `PA-P3_LIVE_VALIDATED`。
5. 三個 Gate B 全通過前，不得宣告 Tick → 1m → 5m pipeline 已可用於每日 paper trading。
6. 之後可進 PA-P4（persistence / bar revision store）或 PA-P5（TOD-RVOL）。PA-P5 需要 20-30 個交易日的歷史分 K，補資料前置時間要提早排。

## 2026-08-16 PA-P4 Bar Persistence + 三方 API 欄位查證

### 三方 API 欄位查證結果

問題：PA-P1~P7 是否需要從三方 API 拿新欄位？若需要，能不能拿得到、欄位是否存在？

逐階段盤點：

| Phase | 需要新的三方欄位？ | 說明 |
|---|---|---|
| PA-P1 | 否 | `TickSTKv1` 欄位已對已安裝 SDK 1.3.2 原始碼查證 |
| PA-P2 | 否 | 輸入只有 `MarketTick` |
| PA-P3 | 否 | 輸入只有 canonical 1m |
| PA-P4 | 否 | 純本地持久化 |
| **PA-P5** | **是** | 需要 20 日「同一時段」歷史分 K 成交量作為 baseline |
| PA-P6 | 否 | 輸入只有 canonical 5m |
| PA-P7 | 否 | 輸入為 5m + RVOL + structure |

也就是**只有 PA-P5 需要新的三方資料源**。

實測（2026-08-16，直接打 `https://api.finmindtrade.com/api/v4/data`）：

```text
TaiwanStockPrice  (start_date/end_date)  -> HTTP 200  rows=2   msg=success
TaiwanStockKBar   (start_date)           -> HTTP 400  msg=Your level is free. Please update your user level.
TaiwanStockKBar   (date)                 -> HTTP 400  msg=Your level is free. Please update your user level.
```

結論：

1. **FinMind 分 K（`TaiwanStockKBar`）需要付費 sponsor 等級**，free tier 直接 400。本機無 `FINMIND_TOKEN`、無 `.env`，因此目前等同 free tier。若使用者持有 sponsor token，需用該 token 重測才能確認。
2. 日 K `TaiwanStockPrice` 匿名可用，欄位為 `date / stock_id / Trading_Volume / Trading_money / open / max / min / close / spread / Trading_turnover`。
3. **D4 的日 K 部分已可結案**：2330 於 2026-08-13 `Trading_money / Trading_Volume = 63854331391 / 26233385 = 2434`，落在當日 2425-2445 區間，故 `Trading_Volume` 單位確為**股**。分 K 的單位仍未知（拿不到資料）。
4. `taiwan_stock_kbar` SDK 簽章為 `(stock_id, stock_id_list, date, timeout, use_async)`，只吃單一 `date`，不吃 `start_date`/`end_date`。既有 adapter 已正確特例處理，非 bug。
5. SDK docstring 宣告分 K 欄位為 `date / minute / stock_id / open / high / low / close / volume`——注意是 `high`/`low`，與日 K 的 `max`/`min` 不同。取得資料後 `_normalize_intraday_bar` 需確認能吃這個 shape。

替代方案（未決定，需使用者選擇）：

- 付費 FinMind sponsor token。
- **Shioaji `api.kbars(contract, start, end)`**：簽章已查證存在且支援日期區間。既有登入路徑可重用。AGENTS.md 禁止的是「盤中反覆 polling kbars 掃全市場」，盤前對候選名單做歷史 backfill 是不同用途，但仍需明確 gate。
- 自行累積：PA-P1 已在寫 raw tick，連續跑 20 個交易日即可自建 baseline。零外部相依，但需要 20 個交易日的前置時間。

**在 baseline 來源決定前不要開始 PA-P5。**

連帶影響：既有 trading-day cycle 的 `--require-intraday-bars` 讀 `data/raw/finmind/TaiwanStockPriceMinute/`，在 free tier 下永遠無法被填入，該旗標目前對任何日期都會 `blocked`。

### PA-P4 實作

新增 `bars.py` 的 append-only event store 與 `tests/test_bar_store.py`（13 tests）。設計文件 `docs/price-action-p4-design.md`。

- layout `data/bars/{timeframe}/{date}/{symbol}.jsonl`；timeframe 是目錄層級，1m / 5m 結構上不可能 key collision，不依賴 key 設計正確。
- correction 是新增一行而非覆寫。記憶體靠 frozen dataclass、磁碟靠 append-only，兩層都保證「已被策略使用的 revision 不被靜默改寫」。
- 沒有做 event 去重：重跑同一場 session 事件數會翻倍，但 `latest_bars()` 以 `(symbol, timeframe, start_at)` 收斂取最大 revision，latest view 不變。有測試守住。
- 刻意不做 TiDB `market_bars` table。bar 是 append-only event，JSONL 就夠；一天約 270 根 1m，掃描成本可忽略。加 table 需要 migration + repository + adapter 測試，換到的只是查詢語法。

CLI 變更：`bars build-1m` 改名為 `bars build`，同時產生並保存 1m 與 5m，新增 `--store-dir`（預設 `data/bars`）。輸出檔名由 `{date}-1m-bars.json` 改為 `{date}-bars.json`，payload 由 `bars` 改為 `bars_1m` / `bars_5m`。相關文件已同步更新。

### 驗證

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # Ran 261 tests, OK
PYTHONPATH=src python3 -m compileall -q src tests
git diff --check
```

測試數：248 → 261。

### Next-run Seed

1. 決定 PA-P5 baseline 來源（付費 token / Shioaji kbars / 自行累積 20 日）。
2. 交易時段跑 `simulate shioaji-tick-smoke`，關閉 PA-P1/P2/P3 三個 Gate B。
3. 之後才進 PA-P6（Swing / 市場結構），該階段無外部 API 相依，可與 PA-P5 的資料等待並行。

## 2026-08-16 PA-P5 TOD-RVOL / Cum-RVOL + Shioaji kbars backfill

### 資料源決定

FinMind 分 K 需付費（見上一則），使用者選定 **Shioaji `api.kbars(contract, start, end)` 盤前 backfill**。

新增 `src/tw_day_trading_lab/backfill.py` 作為 composition layer。放獨立模組的原因是 `bars.py` 只能 import `market_data`，反向 import 會循環；而 backfill 兩邊都要用。

邊界處理：

- AGENTS.md 禁止的是「盤中反覆 polling kbars 掃全市場」。這裡是**盤前、有界、只針對候選名單**的歷史抓取，屬於不同用途，但仍加獨立 gate 且預設關閉。
- 只登入行情（`fetch_contract=True`、`subscribe_trade=False`），拒絕 `simulation != True`，不下單不取消。
- backfill 產出的 bar 標 `source="shioaji_kbars"`，與自行聚合的 `shioaji_tick_aggregated` 永遠可區分。

### kbar 單位未查證的處置

`Kbars` 是欄狀（`ts` 奈秒 / `Open` / `High` / `Low` / `Close` / `Volume` / `Amount`），SDK **沒有**標註 `Volume` 單位。

不猜，改成可被定案：預設 `volume_in_lots=True`（與已查證的 tick 單位一致），並提供 `check_backfill_against_daily()`，用**已確認單位是股**的日 K 作為基準：

```text
ratio = 一日 backfill 分 K volume 總和 / 該日 TaiwanStockPrice.Trading_Volume
ratio ≈ 1     → 正確
ratio ≈ 1000  → 多乘，改 --volume-in-shares
ratio ≈ 0.001 → 少乘
```

`ts` 時區換算同樣未實測，`session_complete.by_day` 會回報每日 bar 數，完整場次應為 270 根，數字不對即是時區或截斷問題。

### PA-P5 核心

`src/tw_day_trading_lab/rvol.py`，輸入只有 canonical `MarketBar`。

兩個關鍵決策：

1. **缺 bar 的日子不算 0**。PA-P2 對無成交分鐘不發 bar；若把缺席當 0，baseline 會被拉低並製造假的量能突破。該日單純不貢獻該 slot 樣本，`SlotStat.days` 記錄實際貢獻天數。
2. **樣本不足時不回傳比值**。`sample_days < min_days` → `status=insufficient_data` 且 `tod_rvol` / `cum_rvol` 皆為 `None`。回傳一個數字就會有人拿去交易。baseline 與 `sample_days` 仍在結果裡供檢查。未知 slot 同樣走這條路，不拋例外；baseline median 為 0 時也回 `None`，不做除以零。

median 優先、mean 保留供對照，符合原計畫文件。

### CLI

新增 `bars backfill-kbars`（預設 blocked）與 `bars rvol`。

同時把 `cmd_simulate_shioaji_tick_smoke` 與新指令重複的 gated 登入流程抽成 `_gated_market_data_login()`——第二份拷貝出現時就該抽。該 helper 只在呼叫端已通過 gate 時才 import SDK。

### 驗證

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # Ran 282 tests, OK
PYTHONPATH=src python3 -m compileall -q src tests
bars backfill-kbars --symbols 2330 ...                 # blocked / enable_kbars_backfill_required / side_effects=[]
bars rvol --date 2026-08-17 --store-dir <empty>        # baseline_days=0 bars=0，不 crash
```

測試數：261 → 282。

### Residual Risks（新增）

7. **kbar volume 單位與 `ts` 時區換算皆未實測**。已備妥 `check_backfill_against_daily` 與 `session_complete` 兩個檢查，但要等真實 backfill 才能定案。
8. **simulation 帳號能否取得 kbars 未實測**。與 PA-P1 Gate B 同一類未知。若取不到，PA-P5 的 baseline 要改回自行累積 20 個交易日的 tick。

### Next-run Seed

1. 交易時段跑 `simulate shioaji-tick-smoke`，關閉 PA-P1/P2/P3 三個 Gate B。
2. 盤前跑 `bars backfill-kbars --enable-kbars-backfill`，用 `check_backfill_against_daily` 定案 volume 單位，關閉 PA-P5 Gate B。
3. PA-P6（Swing / 市場結構）無任何外部 API 相依，可與上述資料等待並行開發。

## 2026-08-16 PA-P6 / PA-P7 / PA-P8

三個階段一起交付，設計文件 `docs/price-action-p6-p8-design.md`。模組相依單向且皆不碰 Shioaji：`bars ← structure ← setup ← paper`，`rvol` 供 `setup` 使用。

### PA-P6 structure.py

- swing 需**兩側各 N 根**確認，因此最新 N 根不會是 swing。當下那根就宣告是 swing 會讓結構隨雜訊翻轉，且 live 與 replay 不一致。
- 嚴格不等：相等的鄰居不算 swing，結果不依賴 tie-break。
- 「missing bar 不會被當成價格 0」在此是結構性成立：輸入是 `MarketBar` 物件，鄰居是清單上的鄰居 bar 而非時鐘上的鄰近 bucket，沒有任何地方會產生 0。
- swing 不足 2 個回 `UNKNOWN` 而不是猜一個趨勢。
- `compute_structure` 是純函式，餵修正後的 bar 就重算，不需額外機制。

寫測試時三個分類 fixture 一開始全掛（回 UNKNOWN），檢查後是我手刻的 zigzag 第二個 swing low 沒形成，不是程式錯。重算成 14 根、swing low 在 index 2/8、swing high 在 5/11 的序列後通過。

### PA-P7 setup.py

狀態機 `WAIT_BREAKOUT → WAIT_RETEST → WAIT_TRIGGER → SIGNAL`，不做 scoring。

兩個關鍵決定：

1. **RVOL 缺失或 `insufficient_data` 一律不 breakout。** 「資料不知道」不等於「通過 volume gate」。三種情況（`rvol is None`、`status != ok`、`tod_rvol is None`）都直接不成立。
2. **同一個 breakout level 每個 symbol 只用一次。** 進場後價格仍在該 swing high 之上，不擋的話下一根會用同一 level 再 breakout。

`setup_id = {date}:{symbol}:{breakout_bar_start_at}:brk`，完全由輸入決定，replay 得到相同 id。entry / stop / target 在 signal 當下全部確定。

### PA-P8 paper.py

四條不可妥協的規則，每條都對應一種自我欺騙：

1. **同一根 bar 內先判 stop 再判 target。** 一根 5m 同時涵蓋兩個價位時模稜兩可；假設好結果會在最該保守的高波動 bar 上灌水 expectancy。
2. **一個 `setup_id` 永遠只成交一次。** 沿用既有 `PaperLedger`，且**不呼叫 `close_intent`**，key 保留一整天，replay 與 restart 都不可能重進同一 setup。
3. **market data 不健康禁止新進場。** 既有部位仍照常管理與強制平倉。
4. **收盤前一定清倉。** `force_exit_at` 強制平倉；跑完所有 bar 若仍有部位，在最後一根以 `pre_close` 平掉。

被擋下的訊號保存在 `skipped[]` 並附理由，否則無法分辨「策略沒觸發」與「被風控擋掉」。

### CLI

新增 `paper run-day`。同時把 `bars rvol` 與它重複的「載入歷史 → 建 baseline → 評分今日」抽成 `_score_symbol_rvol()`。

### 驗證

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # Ran 321 tests, OK
PYTHONPATH=src python3 -m compileall -q src tests
paper run-day --store-dir <不存在>                      # trades: 0 entries: 0，不 crash
```

測試數：282 → 321（structure 16、setup + paper 23）。

### Residual Risks（新增）

9. **PA-P6/P7/P8 的 Gate B 與整條 PA 線共用**，都卡在同一次真實行情驗證。目前只證明「同一份輸入永遠產生同一份輸出，且每條規則照定義執行」，**不是 strategy edge**。
10. 目前只做多。空方 setup、部位大小、成本 / 滑價（`cost.py` 已存在）都尚未接上，屬於 PA-P9。

### Next-run Seed

1. 交易時段跑 `simulate shioaji-tick-smoke` → 關 PA-P1/P2/P3 Gate B。
2. `bars backfill-kbars` + `check_backfill_against_daily` 定案 kbar volume 單位 → 關 PA-P5 Gate B。
3. 上述通過後跑 `paper run-day`，PA-P6/P7/P8 的 Gate B 才有意義。
4. PA-P9：接 `cost.py` 算 net R，做 expectancy 與 ablation 報表。

## 2026-08-16 PA 修正輪：timestamp / health / correction / restart

使用者 review 指出兩件事：Kbars `Volume` 應為「張」（我原本標成未查證，方向其實是對的），而真正危險的是 timestamp。逐項查證後全部成立。

### ① Kbars timestamp — 已存在的實際 bug（最高優先）

實測：

```text
ts = 1779094860000000000（官方 2330 範例，顯示 2026-05-18 09:01）

datetime.fromtimestamp(sec)          → 2026-05-18 17:01   ← 原本的實作
datetime.fromtimestamp(sec, tz=utc)  → 2026-05-18 09:01   ← 正確
pandas.to_datetime(ts)               → 2026-05-18 09:01   ← Shioaji 範例用這個
```

Shioaji 把**交易所本地時間當成 naive UTC epoch** 編碼。原本的實作在 UTC+8 機器上讓每根 backfill bar 偏移 8 小時，PA-P5 所有 time slot 會全錯；更糟的是**結果隨機器時區而異**。已改為 UTC 解碼後取 naive 值。

值得記錄的是：原本的測試 fixture 也用 `moment.timestamp()`（本地時區）編碼 ts，兩邊錯法一致所以測試全綠。fixture 已改成與 Shioaji 相同的編碼，並新增一個直接用官方數值 `1779094860000000000` 的測試。

### Kbars Volume 單位 — 確認是張，不改

官方 2330 範例算術上是決定性的：

```text
當成張：2565 × 1000 × 2230 ≈ 5,719,950,000  ≈ 官方 Amount 5,708,965,000（差 0.19%）
當成股：2565 × 2230        ≈ 5,719,950      差 1000 倍
```

維持 `volume_in_lots=True`。`check_backfill_against_daily` 保留作為實證定案手段。

### ② P1 health 納入 cumulative volume invariant

原本 `volume_checks.consistent` 只在 summary 計算，`health()` 沒用它。因此可能：漏 tick → invariant 已異常 → health 仍 `HEALTHY` → P8 照樣進場。

改成逐筆檢查：接受的 tick 若 `cumulative_volume - 前一筆 != trade_volume`（且非 out-of-order），計入 `volume_gaps`，health 轉 `DEGRADED`。tick 本身仍照常送下游，只有 health 改變。

`volume_gaps` 也是獨立可見的 counter，因此若真實 feed 上它系統性地接近 `raw_ticks`，代表這個不變式假設本身有問題，而不是 feed 壞掉——第一次 live smoke 要看這個數字。

### ③ P7 corrected bar

原本 `state.bars.append(bar)` 無條件追加。PA-P3 會重發 `09:30 rev2`，於是歷史裡會出現兩根同一時間的 bar，直接污染 swing 偵測與 breakout level。

改成 `_store_bar()` 依 `start_at` replace：

- revision 較低或完全相同 → `stale_bars`，忽略。
- revision 較高 → 原地替換，`corrections_applied` +1。

規則依使用者建議保持簡單：**correction 不推進狀態機**（它不是新的時間步）。若當下有進行中的 setup（非 `WAIT_BREAKOUT`），直接 `INVALIDATED` / `corrected_bar_in_setup`，不嘗試從被改動的歷史重建 setup。已發出 SIGNAL 的過去決策不回頭改。

### ④ P8 restart persistence

`PaperLedger` 只在記憶體，crash 後重啟會遺失已進場的 setup。新增 `traded_setups_path`：進場前先把 `setup_id` append 到 JSONL，啟動時載入。重跑同一天會全部 `already_traded` 而不重複成交。

### 驗證

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # Ran 333 tests, OK
PYTHONPATH=src python3 -m compileall -q src tests
```

測試數：321 → 333。新增涵蓋：官方 ts 數值解碼、cumulative 跳號 → DEGRADED、cumulative 相符 → HEALTHY、correction replace 不重複、correction 不推進狀態機、correction 中斷進行中 setup、stale revision 忽略、重覆同一根不算 correction、restart 不重複成交、setup id 落地。

### 剩餘順序

```text
⑤ Kbars volume 實跑交叉驗證（盤前 backfill 後跑 check_backfill_against_daily）
⑥ Gate B 真實交易時段
⑦ PA-P9 expectancy / 成本 / 滑價
```

①～④ 已在非交易日修完。

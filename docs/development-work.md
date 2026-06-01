# Development Work

日期：2026-05-28  
狀態：P0-P6 MVP 已完成；Report Loop / Notification hardening 已完成；Execution Sync MVP 已完成；Shioaji callback normalization MVP 已完成；Shioaji order custom field token mapping、SDK-shaped gateway、callback stream、execution lifecycle policy、duplicate callback dedupe、callback status ordering、execution sync store locking、terminal-state policy、longer callback smoke MVP 已完成；下一步 gated Shioaji simulation smoke
預設分支：`master`

## Phase 對照

本文件採用 P0-P6 作為唯一開發 phase 命名。`docs/mvp-roadmap.md` 必須與本文件保持一致。

| Phase | 名稱 | 目前狀態 | 對應 commit |
|---|---|---|---|
| P0 | Bootstrap 固化 | 已完成 | `4aab4e8` |
| P1 | Old Log / CSV Importer | 已完成 MVP | `579efec` |
| P2 | TiDB Integration | 已完成 MVP | `f614228` |
| P3 | FinMind Nightly Ingestion | 已完成 cache / ledger MVP | `49a917b` |
| P4 | Candidate Engine v1 | 已完成 raw cache candidate builder MVP | `fbdff38` |
| P4b | Candidate Engine Data Enrichment | 已完成 MVP | `2b56adf` |
| P5 | Replay / Paper Ledger | 已完成 MVP | `3c998be` |
| P6 | Shioaji Simulation Adapter | 已完成 dry-run adapter MVP | `a475843` |

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
PYTHONPATH=src python3 -m tw_day_trading_lab.cli report close --date 2026-05-28 --candidates reports/2026-05-28-candidates-from-raw.json --replay reports/2026-03-25-replay.json --simulation reports/2026-05-28-simulation.json --output reports/2026-05-28-close.md --telegram-summary-output reports/2026-05-28-telegram-summary.txt
PYTHONPATH=src python3 -m tw_day_trading_lab.cli notify telegram --date 2026-05-28 --report reports/2026-05-28-close.md --dry-run
```

## 6. Backlog

- 精確定義交易成本、滑價與最小成交金額。
- 定義 `next_day_actionable` 數學條件 v2。
- 設計 TiDB schema migration 流程。
- 決定 raw data 儲存先用 JSONL 還是直接導入 Parquet library。
- 設計 Telegram 正式發送 gate。
- 接真正 Shioaji SDK simulation login / callback streaming。
- 在真正 place order 時將 `idempotency_key` 寫入 order `custom_field`。
- 將 execution sync store 從 dry-run file contract 推進到 callback event ingestion。
- 建立 execution sync persistence，用於 restart 後比對 broker open state 與 ledger state。

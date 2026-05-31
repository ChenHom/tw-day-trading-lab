# Development Work

日期：2026-05-28  
狀態：開發工作基線  
預設分支：`master`

## 0. Grill-me 結論

目前沒有更多阻塞型問題需要先問。已決定：

- 新 repo：`~/services/stock/tw-day-trading-lab`
- 預設分支：`master`
- 最高原則：先產生價值，再擴張架構
- 第一個真實資料工作：舊 log / CSV importer
- 第二個真實資料工作：FinMind nightly ingestion
- 第一階段禁止真實自動下單
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

### P5. Replay / Paper Ledger

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

## 4. Immediate Next Sprint

推薦下一個 sprint：P4b Candidate Engine Data Enrichment。

任務切分：

1. P3 ingestion 改抓 20-50 日窗口，而不是單日，讓 volume expansion / structure 更可靠。
2. 接 `TaiwanStockInfo` 做 ETF / 權證 / 特別股排除。
3. 接法人與融資融券資料，缺資料時降權而不是 crash。
4. 將 cache summary 與 fetch ledger summary 更完整帶入 report。
5. 建立 Top 30-80 fixture 與 regression report。
6. 補 Candidate Engine v1 文件與 tests。
7. commit。

完成標準：

- 候選名單不是買進名單。
- 每檔候選都有 score、archetype、reasons、downgrade_reasons。
- 報告可追溯資料缺口與 API/cache 用量。
- tests 通過。

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
```

## 6. Backlog

- 精確定義交易成本、滑價與最小成交金額。
- 定義 `next_day_actionable` 數學條件 v2。
- 設計 TiDB schema migration 流程。
- 決定 raw data 儲存先用 JSONL 還是直接導入 Parquet library。
- 設計 Telegram 正式發送 gate。
- 建立 Shioaji simulation secrets / config 邊界。

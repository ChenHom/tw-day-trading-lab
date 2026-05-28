# TiDB Integration

日期：2026-05-28<br>
狀態：P2 MVP complete

## 目的

P2 的目的不是把 DB client 塞進策略核心，而是建立一個可以保存與重現研究結果的資料邊界：

- candidate runs
- candidate items
- classified samples：`valid` / `excluded` / `needs_review`
- order intents

Importer / classifier / report renderer 仍維持 pure function。TiDB 只在 CLI composition root 與 repository adapter 邊界被組裝。

## Local TiDB

已驗證本機 TiDB：

```text
host: 127.0.0.1
port: 4000
user: root
version: 8.0.11-TiDB-v8.5.2
status API: http://127.0.0.1:10080/status
```

Python driver：

```text
mysql.connector 9.3.0
```

環境變數：

| 變數 | 預設 |
|---|---|
| `TW_DAYTRADE_DB_HOST` | `127.0.0.1` |
| `TW_DAYTRADE_DB_PORT` | `4000` |
| `TW_DAYTRADE_DB_USER` | `root` |
| `TW_DAYTRADE_DB_PASSWORD` | 空字串 |
| `TW_DAYTRADE_DB_NAME` | `tw_day_trading_lab` |

## CLI

套用 schema，可重複執行：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli db init --schema sql/001_init.sql
```

匯入舊 log 並寫入 TiDB：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli old-logs import \
  --date 2026-03-25 \
  --input examples/old-log.sample.csv \
  --output reports/old-log-sample-samples.json \
  --report-output reports/old-log-sample-failure.md

PYTHONPATH=src python3 -m tw_day_trading_lab.cli samples persist \
  --input reports/old-log-sample-samples.json

PYTHONPATH=src python3 -m tw_day_trading_lab.cli samples summary
```

寫入候選 run：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build \
  --date 2026-05-28 \
  --input examples/candidates.sample.json \
  --output reports/2026-05-28-candidates.json

PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates persist \
  --date 2026-05-28 \
  --input reports/2026-05-28-candidates.json \
  --run-id p2-smoke-2026-05-28 \
  --source sample-fixture \
  --status generated
```

## 函式與功能說明

| 名稱 | 類型 | 說明 |
|---|---|---|
| `StorageError` | exception | DB 操作失敗時明確丟出，不吞錯。 |
| `SampleRepository` | protocol | sample 儲存 port；核心流程只依賴 `save_samples` 與 `fetch_sample_summary`。 |
| `CandidateRepository` | protocol | candidate run 儲存 port；核心流程只依賴 `save_candidate_run` 與 `fetch_candidate_run_summary`。 |
| `TiDBConfig.from_env` | factory | 從環境變數建立 TiDB 連線設定。 |
| `connect_tidb` | IO adapter | 使用 `mysql.connector` 開 TiDB 連線；driver 未安裝或連線失敗會轉成 `StorageError`。 |
| `split_sql_statements` | pure function | 將簡單 SQL script 拆成可逐句執行的 statements。 |
| `apply_schema_script` | IO adapter | 對 TiDB 套用 schema script，失敗 rollback 並丟 `StorageError`。 |
| `apply_schema_file` | IO adapter | 讀取 SQL file 後依設定 database render，再呼叫 `apply_schema_script`。 |
| `render_schema_script` | pure function | 將 schema script 的 database name 換成 `TW_DAYTRADE_DB_NAME`。 |
| `validate_sql_identifier` | pure function | 限制 database name 只能是安全 SQL identifier。 |
| `create_sqlite_schema` | test adapter | 建立 SQLite 測試 schema，讓 repository tests 不依賴 TiDB。 |
| `DatabaseStorage` | adapter | DB-API repository adapter；目前支援 `sqlite` 測試與 `tidb` runtime。 |
| `DatabaseStorage.save_samples` | adapter method | upsert classified samples，保留重複 idempotency evidence。 |
| `DatabaseStorage.fetch_sample_summary` | adapter method | 從 DB 統計 valid / excluded / needs_review 與 exclusion reasons。 |
| `DatabaseStorage.save_candidate_run` | adapter method | 以同一 transaction 寫入 candidate run 與 candidate items。 |
| `DatabaseStorage.fetch_candidate_run_summary` | adapter method | 讀回某個 run 的候選數與 actionable 數。 |
| `sample_to_row` | pure function | 將 sample payload normalize 成 DB 欄位。 |
| `candidate_to_row` | pure function | 將 `CandidateScore` 或 dict normalize 成 candidate item row。 |
| `normalize_datetime_text` | pure function | 將 ISO datetime 的 `T` 轉成 TiDB DATETIME 友善格式。 |
| `load_import_payload` | IO adapter | 讀取 old-log import JSON。 |
| `load_candidate_payload` | IO adapter | 讀取 candidate JSON，支援 `{candidates: [...]}` wrapper。 |
| `persist_import_samples` | composition function | 透過 `SampleRepository` 寫入 samples 並回傳 DB summary。 |
| `persist_candidate_run` | composition function | 透過 `CandidateRepository` 寫入候選 run 並回傳 run summary。 |
| `cmd_db_init` | CLI composition | CLI schema 初始化入口。 |
| `cmd_samples_persist` | CLI composition | CLI sample 寫入入口。 |
| `cmd_samples_summary` | CLI composition | CLI sample summary 查詢入口。 |
| `cmd_candidates_persist` | CLI composition | CLI candidate run 寫入入口。 |

## Schema Decision

`valid_samples.idempotency_key` 在 P2 改成非唯一 index。

理由：P1 的舊 log 反例會刻意保留同 symbol / same timestamp 的 duplicate ENTER。這些樣本的 idempotency key 可能相同；如果 sample table 用唯一鍵，會把污染證據覆蓋掉。

真正的下單冪等性仍由 `order_intents.idempotency_key` 的 primary key 保護。

## 驗證結果

### TDD / unit tests

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
15 tests OK
```

### Compile

```text
PYTHONPATH=src python3 -m compileall -q src tests
OK
```

### TiDB schema

```text
db init run 1: schema applied: tw_day_trading_lab
db init run 2: schema applied: tw_day_trading_lab
```

### TiDB sample persist

```text
valid / excluded / needs_review: 1 / 3 / 1
duplicate_enter_same_symbol_timestamp: 2
```

確認 duplicate idempotency evidence 有保留：

```text
2026-03-25:old-log-v1:4967:legacy_entry:0929:buy -> 2 rows
```

### TiDB candidate persist

```text
run_id: p2-smoke-2026-05-28
item_count: 3
actionable_count: 2
```

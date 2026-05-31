# FinMind Nightly Ingestion

P3 建立 FinMind 夜間資料擷取的最小可行版本。目標不是一次做完整資料湖，而是先確保 API quota 可控、重跑不重複打 API、raw response 可追溯。

## Scope

目前支援：

- `fetch_ledger` 查詢與寫入。
- raw JSONL cache。
- request 可帶 `start_date`，支援 20-50 日歷史窗口。
- 無 token 時回報 setup/auth 缺口，不 crash。
- planned calls quota gate，預設上限 540/hr。
- 同一 `dataset / trading_date / stock_id / source` 已成功且 raw cache 存在時，重跑直接 skip。
- ledger 顯示 success 但 raw cache 遺失時，重新抓取並標記 `refetched_missing_cache`。
- ledger 顯示 success 但 raw cache 不涵蓋 `start_date -> trading_date` 時，重新抓取並標記 `refetched_incomplete_window`。
- API 失敗時寫入 failed ledger row 與 error message。

目前不做：

- 自動產生完整全市場 universe。
- Parquet 寫入。
- 多 dataset pipeline 排程。
- 用 FinMind 盤中即時進出場。

## Cache Contract

P3 cache 分兩層：

| Layer | 位置 | 用途 |
|---|---|---|
| control | TiDB `fetch_ledger` | 判斷 dataset/date/stock/source 是否已成功擷取，並記錄 status / request_count / error_message。 |
| raw data | `data/raw/finmind/{dataset}/{date}/{stock_id}.jsonl` | 保存 API row 原始資料，供後續 candidate engine 或 replay 使用。 |

skip 條件必須同時成立：

1. `fetch_ledger.status = success`
2. raw cache 檔案存在
3. 若 request 有 `start_date`，raw cache 日期範圍必須涵蓋 `start_date -> trading_date`

若只有 ledger success 但 raw cache 不存在，不能假裝資料存在，必須重新擷取。
若 ledger success 但舊 raw cache 只有單日資料，也不能跳過歷史窗口 request。

## CLI

單筆 smoke：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli ingest finmind \
  --date 2026-05-28 \
  --dataset TaiwanStockPrice \
  --stock-id 2330 \
  --cache-dir data/raw
```

使用 request file：

```json
[
  {
    "dataset": "TaiwanStockPrice",
    "start_date": "2026-04-08",
    "trading_date": "2026-05-28",
    "stock_id": "2330"
  },
  {
    "dataset": "TaiwanStockInfo",
    "trading_date": "2026-05-28",
    "stock_id": "market"
  },
  {
    "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
    "start_date": "2026-04-08",
    "trading_date": "2026-05-28",
    "stock_id": "2330"
  },
  {
    "dataset": "TaiwanStockMarginPurchaseShortSale",
    "start_date": "2026-04-08",
    "trading_date": "2026-05-28",
    "stock_id": "2330"
  }
]
```

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli ingest finmind \
  --date 2026-05-28 \
  --requests examples/finmind.requests.sample.json \
  --cache-dir data/raw
```

token 來源：

1. CLI `--token`
2. environment `FINMIND_TOKEN`

## Function Map

| Function / Class | Role |
|---|---|
| `FetchRequest` | 一筆 dataset/date/stock/source 擷取需求。 |
| `raw_cache_path` | 將 request 對應到 raw JSONL cache 路徑。 |
| `cache_satisfies_request` | 檢查舊 raw cache 是否覆蓋 request 的日期窗口。 |
| `ingest_finmind_requests` | P3 orchestration；負責 ledger/cache/quota/error summary。 |
| `FinMindDataLoaderClient` | FinMind SDK adapter；只放在 composition boundary。 |
| `FetchLedgerRepository` | fetch ledger port；核心 ingestion 不依賴 DB client。 |
| `DatabaseStorage.fetch_fetch_record` | 查詢 fetch ledger。 |
| `DatabaseStorage.save_fetch_record` | upsert fetch ledger。 |

## Verification

目前測試涵蓋：

- 無 token 不 crash，且不呼叫 client。
- ledger success + raw cache 存在時第二次執行 skip。
- ledger success 但 raw cache 遺失時重新抓取。
- ledger success 但 raw cache 日期窗口不足時重新抓取。
- planned calls 超過 quota limit 時不呼叫 client。
- client error 會寫入 failed ledger row。

真實 smoke：

- `TaiwanStockPrice` / `TaiwanStockInfo` / `TaiwanStockInstitutionalInvestorsBuySell` / `TaiwanStockMarginPurchaseShortSale` 四筆 request：planned / actual / skipped / failed = 4 / 4 / 0 / 0。
- 同一 request 第二次執行：planned / actual / skipped / failed = 0 / 0 / 4 / 0。

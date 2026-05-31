# Candidate Engine v1

P4 將 P3 的 FinMind raw JSONL cache 轉成隔日候選排序。這仍然不是買進清單，而是「明天值得監控」的研究輸出。

## Scope

目前支援：

- 從 `data/raw/finmind/TaiwanStockPrice/{date}/*.jsonl` 建立候選輸入。
- 用成交金額做第一層 universe / liquidity filter。
- 讀取 `TaiwanStockInfo` 排除 ETF / ETN / 權證等非普通股。
- 讀取法人買賣超與融資融券資料，作為題材強度與擁擠風險調整。
- 從日 K row 推估：
  - `trading_money`
  - `change_pct`
  - `intraday_range_pct`
  - `volume_expansion`
  - `structure_quality`
  - `crowding_risk`
- 單日資料不足時不 crash，標記 `data_quality = degraded` 並在 ranking 降權。
- 缺法人或融資融券資料時不 crash，標記 `data_quality = degraded` 並在 report 記錄缺口。
- malformed raw file 記入 `data_gap_files`，不讓流程中斷。
- candidate JSON payload 帶 `summary`，daily report 會列出資料來源與資料缺口。

目前不做：

- 題材群組強度真實計算。
- Top 80 全市場 production universe。
- 買賣訊號或下單。

## CLI

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build-from-raw \
  --date 2026-05-28 \
  --cache-dir data/raw \
  --output reports/2026-05-28-candidates-from-raw.json
```

產生日報：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli report daily \
  --date 2026-05-28 \
  --input reports/2026-05-28-candidates-from-raw.json \
  --format md \
  --output reports/2026-05-28-daily-from-raw.md
```

## Function Map

| Function / Class | Role |
|---|---|
| `build_candidates_from_raw_cache` | P4 orchestration；讀 raw cache、過濾低流動性、轉 candidate input、排序。 |
| `candidate_from_price_rows` | 將一檔股票的 FinMind daily price rows 轉成 `CandidateInput`。 |
| `load_stock_info` | 讀取市場層級 stock info raw cache。 |
| `is_non_common_stock` | 排除 ETF / ETN / 權證等非普通股工具。 |
| `enrich_candidate` | 使用 stock info、法人、融資融券資料補名稱、題材強度、擁擠風險與 data quality。 |
| `CandidateBuildResult` | 保存 candidates、ranked candidates 與 source summary。 |
| `cmd_candidates_build_from_raw` | CLI composition root。 |

## Data Quality

`data_quality` 規則：

- `ok`：有目標日資料、至少一筆前序日資料，且法人 / 融資融券 enrichment 可用。
- `degraded`：只有目標日資料，或缺法人 / 融資融券 enrichment；仍可輸出候選，但 ranking 會加入 `data_quality_degraded` 降權原因。
- malformed：缺必要價格欄位時不輸出候選，summary 的 `data_gap_files` 加一。

## Verification

目前測試涵蓋：

- 可從 price cache 建立 ranked candidates。
- 低成交金額先被 universe filter 擋掉。
- 單日資料會標成 degraded，不 crash。
- malformed raw row 會記入 data gap。
- ETF / ETN / 權證等非普通股會被排除。
- 法人與融資融券資料完整時可保持 `data_quality = ok`。
- 缺法人或融資融券資料時會降權，不 crash。
- daily report 會顯示 source summary 與資料缺口。

真實 smoke：

- 使用 P3 raw cache 建立 `2330` 2026-05-28 candidate。
- source summary：input files 1、built candidates 1、degraded candidates 0、data gap 0、missing chip 0、missing margin 0。

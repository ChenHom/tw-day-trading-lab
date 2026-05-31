# Data Contracts

## Candidate Input

候選輸入代表夜間或盤中資料產生出的候選標的。這不是買進清單。

必要欄位：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `symbol` | string | 股票代號 |
| `name` | string | 股票名稱 |
| `trading_money` | number | 成交金額 |
| `change_pct` | number | 日漲跌幅百分比 |
| `intraday_range_pct` | number | 日內振幅百分比 |
| `volume_expansion` | number | 量能擴張倍數 |
| `theme_strength` | number | 族群/題材強度 0-1 |
| `structure_quality` | number | 結構品質 0-1 |
| `crowding_risk` | number | 擁擠風險 0-1 |
| `data_quality` | string | `ok` / `missing` / `stale` |

## Candidate Output

| 欄位 | 說明 |
|---|---|
| `rank` | 排名 |
| `total_score` | 總分 0-100 |
| `archetype` | `breakout_continuation` / `expansion_from_base` / `theme_follower` |
| `next_day_actionable` | 是否值得隔日監控 |
| `reasons` | 排序理由 |
| `downgrade_reasons` | 降權或禁做原因 |

## Valid Strategy Sample

有效策略樣本必須有完整 lifecycle：

- candidate source
- strategy id
- setup id
- idempotency key
- entry / exit timestamp
- entry / exit price
- MFE / MAE
- gross R / cost R / net R
- validity
- exclusion reason

策略 expectancy 只能使用 `validity = valid` 的樣本。

## Persisted Strategy Samples

TiDB `valid_samples` table 目前保存 classified strategy samples。雖然沿用 `valid_samples` 名稱，實際內容包含：

- `valid`
- `excluded`
- `needs_review`

`idempotency_key` 在 sample table 是非唯一 index，因為舊 log 反例需要保留 duplicate ENTER 的污染證據。真正防止重複下單的唯一約束放在 `order_intents.idempotency_key`。

新增欄位：

| 欄位 | 說明 |
|---|---|
| `position_id` | 舊 log position lifecycle id |
| `event_count` | entry + exits event 數 |
| `warnings` | sample 級警告 JSON |

## Candidate Persistence

TiDB candidate tables：

| Table | 說明 |
|---|---|
| `candidate_runs` | 每次候選產生的 run metadata |
| `candidate_items` | 每個 run 內的 ranked candidates |

`candidate_items` 使用 `(run_id, symbol)` 作為 primary key，重跑同一 run 會更新同一批候選，不會重複插入。

## FinMind Fetch Ledger

TiDB `fetch_ledger` 是 API cache 的 control layer。它不保存 raw data，只保存是否已經抓過、狀態與錯誤。

Primary key：

- `dataset`
- `trading_date`
- `stock_id`
- `source`

欄位：

| 欄位 | 說明 |
|---|---|
| `dataset` | FinMind dataset 名稱，例如 `TaiwanStockPrice` |
| `trading_date` | 資料日期 |
| `stock_id` | 股票代號；市場級資料可用 `market` |
| `source` | 預設 `finmind` |
| `status` | `success` / `failed` |
| `request_count` | 該 ledger row 對應的 API request 次數 |
| `error_message` | failed 時保存錯誤原因 |
| `fetched_at` | 最後寫入時間 |

raw cache 放在：

```text
data/raw/finmind/{dataset}/{trading_date}/{stock_id}.jsonl
```

skip 條件必須同時滿足 `fetch_ledger.status = success` 與 raw cache 檔案存在。
若 request 帶 `start_date`，raw cache 還必須覆蓋 `start_date -> trading_date` 的日期窗口；舊單日 cache 不可直接視為完整窗口。

## Old Log Import Output

`tw-daytrade old-logs import` 會輸出：

| 欄位 | 說明 |
|---|---|
| `source_path` | 匯入來源 CSV |
| `events_count` | CSV row 事件數 |
| `summary` | valid / excluded / needs_review 與原因統計 |
| `duplicate_enter_groups` | 同 symbol 同 timestamp 的 ENTER 重複群組 |
| `samples` | `valid_samples` compatible sample list |

`samples[].validity` 規則：

- `valid`：完整 entry / exit lifecycle，可納入後續策略統計。
- `excluded`：明確不可納入 expectancy，例如 duplicate enter、debug / forced / after-hours。
- `needs_review`：資料不足或格式異常，需要人工或後續工具審查。

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


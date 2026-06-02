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

## Replay Output

`tw-daytrade replay samples` 會輸出：

| 欄位 | 說明 |
|---|---|
| `summary.total_samples` | 輸入 sample 總數 |
| `summary.replayed` | 實際納入 replay 的 `valid` 樣本數 |
| `summary.skipped_excluded` | 跳過的 excluded 樣本數 |
| `summary.skipped_needs_review` | 跳過的 needs_review 樣本數 |
| `summary.skipped_duplicate_idempotency` | 因同一 `idempotency_key` 重複而跳過的 valid 樣本數 |
| `summary.expectancy_gross_r` | 只用 replayed trades 計算的 gross R 平均 |
| `summary.average_cost_r` | replayed trades 的平均 cost R |
| `summary.expectancy_net_r` | gross R 扣除 cost R 後的平均 |

Replay trade row：

| 欄位 | 說明 |
|---|---|
| `sample_id` | 來源 sample id |
| `symbol` | 股票代號 |
| `idempotency_key` | replay 去重 key |
| `realized_r_gross` | 成本前 R |
| `estimated_cost_r` | 成本 / 滑價假設 R |
| `realized_r_net` | 成本後 R |
| `mfe_r` | 最大有利幅度 R |
| `mae_r` | 最大不利幅度 R |

## Simulation Input

`tw-daytrade simulate run` 的輸入可為 list，或 `{items: [...]}` / `{signals: [...]}` wrapper。
每個 item 可包含 `signal` 與 `risk_decision`：

`signal` 必要欄位：

| 欄位 | 說明 |
|---|---|
| `trading_date` | 交易日 |
| `strategy_id` | 策略 id |
| `symbol` | 股票代號 |
| `setup_id` | setup / entry lifecycle id |
| `side` | `buy` / `sell` |
| `quantity` | 模擬委託股數 |
| `price` | 模擬委託價，可為 null |

`risk_decision` 欄位：

| 欄位 | 說明 |
|---|---|
| `approved` | 是否通過風控 |
| `reason` | 風控理由 |
| `quantity` | 風控後股數，可省略 |
| `price` | 風控後價格，可省略 |

## Simulation Output

simulation output 必須和 replay output 分開。`sample_type` 固定為 `simulation`，且目前 `expectancy_eligible=false`，不得自動併入 replay expectancy。

| 欄位 | 說明 |
|---|---|
| `summary.total` | simulation item 總數 |
| `summary.expectancy_eligible` | 可納入 expectancy 的數量；P6 MVP 固定為 0 |
| `results[].signal` | 原始 `SignalIntent` |
| `results[].risk_decision` | 風控決策 |
| `results[].order_intent` | 由 signal 轉出的 `OrderIntent` |
| `results[].broker_trade` | broker dry-run 回報 |
| `results[].ledger_position` | ledger open position snapshot |
| `results[].status` | `simulated` / `duplicate` / `risk_rejected` / `broker_rejected` / `needs_review` |
| `results[].review_reason` | duplicate 或 mismatch 等審查原因 |

Broker / ledger reconciliation output：

| 欄位 | 說明 |
|---|---|
| `checked` | 檢查的 broker trade 數 |
| `matched` | broker trade 與 ledger open intent 一致的數量 |
| `needs_review` | 狀態不一致或未知 broker status 的數量 |
| `samples[].validity` | `valid` 或 `needs_review` |
| `samples[].expectancy_eligible` | 固定為 false，避免 simulation 污染策略統計 |

## Execution Sync Store

`tw-daytrade simulate run --execution-sync-store ...` 會保存 dry-run execution state，用於 restart 後同步檢查。這不是策略績效資料，也不得納入 replay expectancy。

Top-level 欄位：

| 欄位 | 說明 |
|---|---|
| `broker_trades` | broker 回報的 normalized trades |
| `open_positions` | ledger open position snapshot |
| `results` | simulation result payload |
| `callback_events` | normalized Shioaji callback events |
| `lifecycle_decisions` | callback 對 ledger lifecycle 的決策紀錄 |
| `callback_event_keys` | 已保存 callback 的去重 key |
| `callback_status_by_order` | 同一 broker order 目前已接受的最後狀態 |
| `callback_ordering_issues` | 被跳過的 stale callback 狀態紀錄 |
| `custom_field_map` | Shioaji 短 `custom_field` token 到完整 `idempotency_key` 的 mapping |

`tw-daytrade simulate restart-sync --store ...` 會讀取 execution sync store，重建 `PaperLedger` open keys，並比對 broker trades 與 ledger open positions。

寫入規則：

- `FileExecutionSyncStore` 對 `record_result` 與 `record_callback_event` 使用同一個 `.lock` 檔做 exclusive file lock。
- lock 會包住 read-modify-write，避免多個 callback writer 同時讀到舊 snapshot 後互相覆蓋。
- JSON store 寫入使用同目錄 temp file，再用 atomic replace 換到正式路徑；避免讀到半寫入內容。

Restart sync report：

| 欄位 | 說明 |
|---|---|
| `checked` | 比對項目數，包含 broker trades 與 ledger-only open positions |
| `matched` | broker trade 與 ledger open intent 一致的數量 |
| `needs_review` | 需要人工檢查的數量 |
| `samples[].review_reason` | `broker_status_needs_review` / `ledger_missing_open_intent` / `ledger_missing_broker_trade` 等原因 |

目前 MVP 邊界：

- 只使用 dry-run simulation output 驗證 restart sync contract。
- 已可 normalize Shioaji callback payload，但尚未連接真正 SDK streaming。
- 若 broker / ledger 不一致，一律 `needs_review`，不自動修正 state。

## Shioaji Callback Normalization

`tw-daytrade simulate ingest-callback` 讀取 `{stat, msg}` JSON，轉成標準 callback event，並寫入 execution sync store。

`tw-daytrade simulate callback-smoke` 讀取 callback sequence JSON，依序 normalize 並寫入 execution sync store，輸出 accepted / skipped summary、逐筆 normalized event 摘要與 ordering issues。這個 smoke 用來驗證多事件序列，不登入 Shioaji、不送單。

## Shioaji Order Request Contract

Shioaji order request adapter 目前只定義 dry-run / fake gateway contract，不匯入 SDK、不登入真實帳號、不送出真實委託。

Order request 欄位：

| 欄位 | 說明 |
|---|---|
| `trading_date` | 交易日 |
| `symbol` | 股票代號 |
| `side` | `buy` / `sell` |
| `quantity` | 風控後股數，若未覆寫則使用 signal quantity |
| `price` | 風控後價格，若未覆寫則使用 signal price |
| `idempotency_key` | 完整 `OrderIntent.idempotency_key`，保存在本地 contract |
| `custom_field` | 由 `idempotency_key` 產生的 6 字元 Shioaji token |
| `price_type` | `LMT` 或 `MKT` |
| `order_type` | 預設 `ROD` |

規則：

- `build_shioaji_order_request` 是唯一把 `OrderIntent` 轉成 Shioaji order request 的邊界。
- Shioaji SDK `custom_field` 最長 6 字元，因此不能直接寫入完整 `idempotency_key`；系統用穩定 6 字元 token 寫入 `custom_field`，並把 `custom_field -> idempotency_key` mapping 存在 execution sync store。
- `ShioajiOrderRequestBroker` 只依賴 gateway protocol，可用 fake gateway 驗證 request contract；真實 SDK adapter 必須遵守同一 contract。
- `ShioajiSdkSimulationGateway` 已可用 fake SDK 驗證 login / `api.Order` / `api.place_order` 的 SDK-shaped 邊界，不會在測試中登入真實帳號或送真實委託。
- `ShioajiSdkSimulationGateway` 只接受 `api.simulation=True` 或沒有該屬性的 fake API；若偵測到 `api.simulation=False` 會直接拒絕。
- `ShioajiCallbackStream` 可註冊 `api.set_order_callback`，收到 callback 後讀取 execution sync store 的 `custom_field_map`、normalize event、寫回 `callback_events` 與可轉換的 `broker_trades`。
- `ShioajiCallbackStream` 同樣只接受 `api.simulation=True` 或沒有該屬性的 fake API；若偵測到 `api.simulation=False` 會直接拒絕。
- 若後續 callback 的短 `custom_field` 找不到 mapping，callback normalization 會標為 `unresolved_custom_field`。

Callback event 欄位：

| 欄位 | 說明 |
|---|---|
| `stat` | Shioaji callback 的 stat |
| `broker_order_id` | broker order id |
| `idempotency_key` | 由 callback payload 明示欄位，或由短 `custom_field` mapping 還原 |
| `trading_date` | CLI 提供的交易日 |
| `symbol` | 股票代號 |
| `side` | `buy` / `sell` |
| `quantity` | 委託或成交數量 |
| `price` | 委託或成交價格 |
| `normalized_status` | `filled` / `partial_filled` / `submitted` / `cancelled` / `rejected` / `needs_review` |
| `raw_status` | broker 原始狀態 |
| `review_reason` | 缺欄位或未知狀態原因 |
| `raw` | 原始 callback payload |

Normalization 規則：

- 缺 `idempotency_key`、短 `custom_field` 無法還原、缺 `broker_order_id` 或缺 `symbol` 會標為 `needs_review`。
- 未知 broker status 會標為 `broker_status_needs_review`。
- callback-only event 若沒有對應 ledger open position，restart-sync 會輸出 `ledger_missing_open_intent`。

Lifecycle policy：

| callback status | ledger effect | action |
|---|---|---|
| `submitted` | `none` | `keep_pending_order` |
| `filled` | `open_position` | `confirm_open_position` |
| `partial_filled` | `hold_for_review` | `partial_fill_manual_reconciliation` |
| `cancelled` | `close_intent` | `cancelled_release_intent` |
| `rejected` | `close_intent` | `rejected_release_intent` |
| `needs_review` | `hold_for_review` | `callback_needs_review` |

目前 MVP 只記錄 lifecycle decision，不自動改寫 `open_positions`；partial fill / cancel / reject 的實際 position mutation 留到後續明確策略化。

Callback 去重：

- execution sync store 會用 `trading_date | broker_order_id | idempotency_key | normalized_status | quantity | price` 建立 callback event key。
- 同一 key 重複進來時，`callback_events`、`broker_trades`、`lifecycle_decisions` 都不會重複寫入。
- 同一 broker order 另外用 `trading_date | broker_order_id | idempotency_key` 追蹤最後已接受狀態。
- 狀態 precedence：`submitted < partial_filled < filled < cancelled / rejected < needs_review`。
- 若後到 callback 的 precedence 小於目前已接受狀態，會寫入 `callback_ordering_issues` 並跳過，不新增 `callback_events`、`broker_trades` 或 `lifecycle_decisions`。
- `filled`、`cancelled`、`rejected` 視為 terminal callback status；同一 broker order 已接受其中一個終態後，若又收到不同終態，會寫入 `callback_ordering_issues.reason = terminal_state_conflict` 並跳過。
- terminal-state conflict 不會新增 `callback_events`、`broker_trades` 或 `lifecycle_decisions`，避免把終態衝突當成正常 lifecycle progression。

## Production Readiness Report

`tw-daytrade simulate production-readiness` 讀取 execution sync store，輸出正式營運前的 gate / alert / manual action report。這個命令不登入、不送單、不取消，只做 readiness 判斷。

Input：

| 欄位 / 參數 | 說明 |
|---|---|
| `--date` | readiness report 的交易日 |
| `--store` | execution sync store |
| `--current-time` | 可選，檢查是否在 `09:00-13:20` regular session |
| `--allow-live-trading` | 只表示 readiness gate 願意評估 live trading；不會觸發下單 |
| `--manual-approval-token` | 人工確認 token |
| `--expected-manual-approval-token` | 可選，預設為 `{date}:LIVE-TRADING-APPROVED` |
| `--max-pending-orders` | 允許未終態 submitted order 數，預設 0 |
| `--disable-cancel-retry-plan` | 關閉 cancel retry plan 檢查，預設不建議 |

Output：

| 欄位 | 說明 |
|---|---|
| `status` | `ready` / `blocked` / `needs_review` |
| `live_execution_allowed` | 只有所有 checks OK 時才為 true |
| `summary.checks` | 檢查項目數 |
| `summary.ok` / `blocked` / `needs_review` | readiness check 統計 |
| `summary.pending_orders` | 尚未進終態的 submitted order 數 |
| `summary.partial_fills` | partial fill lifecycle decision 數 |
| `summary.ordering_issues` | callback ordering issue 數 |
| `checks[]` | 每個 readiness check 的狀態、severity、review reason、detail |
| `alerts[]` | 非 OK checks 的告警摘要 |
| `manual_actions[]` | 操作者下一步清單 |

Checks：

| check | OK 條件 |
|---|---|
| `formal_live_gate` | `--allow-live-trading` 且人工 token 等於 expected token |
| `regular_session_policy` | 未提供 `--current-time`，或時間在 `09:00-13:20` |
| `pending_order_limit` | pending orders <= `--max-pending-orders` |
| `partial_fill_policy` | 沒有 partial fill lifecycle decision |
| `callback_ordering` | 沒有 callback ordering issue |
| `cancel_retry_plan` | 沒有 pending order 或 failed cancel result；除非明確關閉檢查 |

安全邊界：

- readiness report 是正式營運前的 blocker，不是正式下單功能。
- 未提供人工 approval token 時，`formal_live_gate` 必須 blocked。
- P7 只輸出 JSON / Markdown report；正式告警發送、live broker adapter 與人工操作 SOP 留下一個 phase。

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

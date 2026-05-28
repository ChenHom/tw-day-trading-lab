# MVP Roadmap

## Phase 0: Repo Bootstrap

- 建立乾淨 repo。
- 建立 README、資料契約、TiDB schema、CLI skeleton。
- 放入 sample candidate fixture。
- 測試候選排序、報告輸出、ledger idempotency。

## Phase 1: Candidate DB

- FinMind nightly ingestion。
- Shioaji scanner / snapshot ingest。
- fetch ledger / API quota ledger。
- Parquet raw data storage。
- TiDB candidate metadata。

## Phase 2: Candidate Engine

- universe 過濾。
- broad pool first。
- archetype 分類。
- 多維排序。
- `next_day_actionable` 初版 label。

## Phase 3: Report Loop

- 每日日報。
- Telegram 摘要 dry-run。
- 前一日候選驗證。
- valid / excluded / needs_review 樣本統計。

## Phase 4: Replay / Paper Ledger

- historical replay。
- paper ledger。
- 成本、滑價、R 值計算。
- 同一 setup 不重複開倉。

## Phase 5: Shioaji Simulation

- `SignalIntent -> RiskDecision -> OrderIntent -> BrokerTrade -> LedgerPosition`。
- 委託回報與重啟同步。
- simulation 樣本與 replay 樣本分開統計。


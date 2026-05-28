# 台股當沖重建基線

日期：2026-05-28

## 命名與 repo 決策

新專案 repo：`~/services/stock/tw-day-trading-lab`

原因：

- `lab` 表示第一階段是候選、回放、樣本與執行鏈路驗證。
- 不把舊 live bot 的研究、下單、績效分析耦合帶進來。
- 等候選與策略樣本證明有價值後，再決定是否拆正式 execution service。

## 舊專案主要失敗原因

1. 策略期望值沒有穩定證明。
   - 2026-03-18 勝率 60%，但盈虧比只有 0.62:1，expectancy 為負。
   - 2026-03-25、03-26、04-01 出現連續 -R 與高停損比例。

2. 候選名單目標錯位。
   - 舊系統偏 `strong close ranking`。
   - 新系統要做 `next_day_actionable ranking`。

3. 交易生命週期污染。
   - 舊 CSV 出現同秒同標的雙 `ENTER`。
   - 新系統必須用 `idempotency_key` 與 ledger 防重複開倉。

4. 研究、訊號、下單、績效分析耦合。
   - 新系統必須先切開資料、候選、回放、風控、執行與 ledger。

## 最高原則

First, achieve the goal of producing value as the top priority.

本專案的第一個價值不是漂亮架構，而是每天更快回答：

- 明天哪些標的值得看？
- 昨天候選是否真的形成可交易機會？
- replay / simulation 樣本是否乾淨累積？
- 哪些策略與候選規則應該被淘汰？

## 第一階段交付

- CLI 產生候選名單。
- HTML / Markdown 日報。
- Telegram 摘要 dry-run。
- TiDB metadata schema。
- Parquet raw data layout。
- Paper ledger 防重複開倉。

## 不做

- 真實自動下單。
- dashboard。
- 用 simulation 成交結果直接宣稱策略有效。
- 用 raw signal log 直接算績效。


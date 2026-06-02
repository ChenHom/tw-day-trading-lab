# MVP Roadmap

本文件的 Phase 命名必須和 `docs/development-work.md` 的 P0-P7 保持一致。若實作狀態改變，優先同步這兩份既有文件，不另開新的 phase 狀態文件。

## Phase Review Rule

每個 phase / sprint 完成後，必須執行 grill-me review，確認實作方向、驗證範圍與文件狀態沒有偏離。review 結論必須寫回 `docs/development-work.md` 對應 phase；若 roadmap 狀態改變，才同步更新本文件。

## P0: Repo Bootstrap

目前狀態：已完成。

- 建立乾淨 repo。
- 預設分支使用 `master`。
- 建立 README、資料契約、TiDB schema、CLI skeleton。
- 放入 sample candidate fixture。
- 測試候選排序、報告輸出、ledger idempotency。

## P1: Old Log / CSV Importer

目前狀態：已完成 MVP。

- 匯入舊交易 log / CSV。
- 將樣本分成 `valid`、`excluded`、`needs_review`。
- 標記 debug、強制下單、盤後測試、重複開倉、缺 exit、ledger 污染。
- 產生第一版 failure replay report。
- 驗證 paper ledger 的 `idempotency_key` 能重現並擋住雙 `ENTER`。

## P2: TiDB Integration

目前狀態：已完成 MVP。

- TiDB candidate metadata：已可保存 candidate runs / items。
- TiDB classified samples：已可保存 valid / excluded / needs_review，並保留 duplicate ENTER evidence。
- `fetch_ledger` schema 已存在，供 P3 API cache 使用。
- `order_intents.idempotency_key` 是真正的下單冪等性邊界。

## P3: FinMind Nightly Ingestion

目前狀態：已完成 cache / ledger MVP。

- FinMind nightly ingestion：已完成 fetch ledger + raw JSONL cache MVP。
- 同一 dataset/date/stock/source 重跑可 skip，避免重複打 API。
- planned calls 預設上限 540/hr。
- 無 token 不 crash，回報 `auth_missing`。
- 真實 FinMind smoke 已確認第一次 actual=1、第二次 skipped=1。
- 下一步需把單日擷取擴成 20-50 日窗口。

## P4: Candidate Engine v1

目前狀態：raw JSONL candidate builder MVP 與 P4b enrichment MVP 已完成。

- universe 過濾。
- broad pool first。
- archetype 分類。
- 多維排序。
- `next_day_actionable` 初版 label。
- FinMind price raw cache 轉候選：已完成。
- daily report source summary / data gaps：已完成。
- P4b 是 P4 的資料強化，不是獨立大 phase：20-50 日窗口、`TaiwanStockInfo`、法人、融資融券 MVP 已完成。

## P5: Replay / Paper Ledger

目前狀態：已完成 MVP。

- historical replay。
- paper ledger：P0 骨架已完成。
- 成本、滑價、R 值計算：已完成 MVP，以 `cost_r` 假設表示。
- 同一 setup 不重複開倉：ledger 已有；replay 另會 skip duplicate idempotency。
- replay expectancy 只使用 `validity = valid`：已完成。
- gross / cost / net R 分開報表：已完成。

## P6: Shioaji Simulation

目前狀態：已完成。

- P6A 本地 simulation execution chain：已完成 `SignalIntent -> RiskDecision -> OrderIntent -> BrokerTrade -> LedgerPosition` dry-run chain，simulation 與 replay expectancy 分開，duplicate intent 會在 broker order 前被 ledger 擋下。
- P6B callback / restart-sync hardening：已完成 execution sync store、restart-sync、callback normalization / stream、lifecycle policy、duplicate dedupe、status ordering、store locking、terminal-state policy、longer callback smoke。
- P6C gated Shioaji simulation login + callback registration：已完成真實 `sj.Shioaji(simulation=True)` login smoke，`set_order_callback` 可註冊，且所有 gateway 都拒絕 `api.simulation=False`。
- P6D gated simulation order + cancel smoke：已完成真實 simulation order / callback / cancel smoke。最終 smoke summary total 4、ok 4、blocked 0；order status `submitted`，cancel callback `cancelled`，restart-sync matched 2、needs_review 0。
- 安全邊界：正式區登入、正式委託與自動下單仍不屬於本階段；P6 只證明 Shioaji simulation 執行鏈路可控，不證明策略 edge。

## P7: Production Readiness Gate

目前狀態：已完成。

- 新增 `simulate production-readiness`，從 execution sync store 產生 production readiness JSON / Markdown report。
- readiness checks 包含正式 live gate、regular-session policy、pending order limit、partial fill policy、callback ordering、cancel retry plan。
- 正式 live gate 預設 blocked；只有 `--allow-live-trading` 搭配正確 `--manual-approval-token` 才會讓 readiness report 進入 `ready`，且此命令本身不送單。
- pending submitted order、partial fill、callback ordering issue、cancel retry 缺口都會轉成 alerts 與 manual actions。
- P7 smoke 已用 P6 真實 simulation store 驗證：checks 6、ok 5、blocked 1、needs_review 0；唯一 blocker 是未提供正式人工 approval token。
- 安全邊界：P7 只完成正式營運前的 gate/report/alert contract，不啟用正式下單。

## Later: Report Loop / Notification

目前狀態：hardening 已完成。

- 每日日報：已完成 candidate daily report。
- 單日 close report：已完成，可聚合 candidate / replay / simulation output。
- Telegram 摘要 dry-run：已完成專用 summary renderer，仍禁止真實發送。
- 前一日候選驗證。
- valid / excluded / needs_review 樣本統計：已透過 replay 與 close report 顯示。
- simulation status summary：已完成，`needs_review` 會明確列出，且 simulation 不納入 replay expectancy。
- hardening：逐筆 `needs_review` reason、專用 Telegram summary renderer、壞檔 / 缺欄位測試已完成第一版。
- 下一步：若要進正式營運設計，先定義 approval token 流程、實際 live broker adapter、告警發送與人工操作 SOP；不要把這些混回 P7。

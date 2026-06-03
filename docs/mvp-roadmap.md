# MVP Roadmap

本文件的 Phase 命名必須和 `docs/development-work.md` 保持一致。若實作狀態改變，優先同步這兩份既有文件，不另開新的 phase 狀態文件。

## Phase Review Rule

每個 phase / sprint 完成後，必須執行 grill-me review，確認實作方向、驗證範圍與文件狀態沒有偏離。review 結論必須寫回 `docs/development-work.md` 對應 phase；若 roadmap 狀態改變，才同步更新本文件。

P8 之後每個 phase close-out 不能只列「新增命令」或「新增文件」，必須同時通過：

- command gate：有可重跑命令。
- artifact gate：產出可追蹤 artifact / manifest。
- regression gate：失敗情境可轉成測試或 fixture。
- operator gate：報告或 alert 明確告訴操作者下一步。

## Cross-phase Failure Controls

這些控制點橫跨 P0-P11，不屬於單一 phase，但任何後續 phase 都必須遵守。

- 已完成不等於無風險：P0-P7 仍可能有 residual risk，後續 phase 不可假設舊 phase 完美。
- 每個 ops artifact 必須可追溯到 run id / source input / command / checksum，否則 P10 regression 無法成立。
- simulation / readiness / live execution 都不得被當成 strategy edge；strategy edge 只能由合格 replay / research sample 證明。
- readiness report 在 P7 只是 report contract；真正的 live execution enforcement 必須在 P11 broker boundary 再做一次，不可只信 report。
- approval token 不可使用可預測字串作為正式密鑰；P7 的預設 token 只適合測試 gate，不適合正式營運。

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
- 20-50 日窗口已在 P4b enrichment 完成；P3 不再保留此待辦。

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
- residual risk：P7 readiness 目前是 report，不是 broker boundary enforcement；P11 必須在 live adapter 再實作硬阻擋。
- residual risk：P7 預設 expected token 是可預測字串，只能用於測試；正式 approval token 必須改成不可預測、短效、可稽核的人工授權。

## Later: Report Loop / Notification

目前狀態：hardening 已完成。

- 每日日報：已完成 candidate daily report。
- 單日 close report：已完成，可聚合 candidate / replay / simulation output。
- Telegram 摘要 dry-run：已完成專用 summary renderer，仍禁止真實發送。
- 前一日候選驗證。
- valid / excluded / needs_review 樣本統計：已透過 replay 與 close report 顯示。
- simulation status summary：已完成，`needs_review` 會明確列出，且 simulation 不納入 replay expectancy。
- hardening：逐筆 `needs_review` reason、專用 Telegram summary renderer、壞檔 / 缺欄位測試已完成第一版。
- 下一步：進 P8-P11，不要把 simulation go-live、reports、regression correction 與正式下單設計混成一個 phase。

## P8: Simulation Ops Go-live

目前狀態：已完成。

目標：把 P6/P7 的 gated simulation order chain 變成每日可執行的 simulation ops，不啟用正式區。

- 建立 daily simulation runner：candidate input -> risk decision -> Shioaji simulation order -> callback stream -> restart-sync -> readiness report。
- 支援 dry-run / simulation-on 的雙 gate，預設不送 simulation order。
- 產生單日 execution bundle，包含 input plan、order report、callback store、restart-sync、production-readiness。
- 設定 trading-day / regular-session gate 與盤後禁止策略。
- must-fix：定義 plan builder contract，明確記錄 candidate source、risk decision reason、limit price source、quantity source、blocked reason。
- must-fix：送 simulation order 前檢查 limit_up / limit_down、reference price、contract loaded、regular session、trading day。
- must-fix：建立 `ops_run_manifest`，保存 run id、artifact path、input/output checksum、side effects summary。
- must-fix：P8 完成條件不是「能送 simulation order」，而是 daily run 可從 manifest replay / audit / explain。
- 驗收：可在一個交易日完成 end-to-end simulation run，且所有 side effects、blocked reasons、broker order ids 都可追蹤。

## P9: Ops Reports / Alerts

目前狀態：待處理。

目標：讓每日 simulation ops 結果能被快速看懂與追蹤。

- 把 candidate / replay / simulation / restart-sync / readiness 合成 daily ops close report。
- Telegram summary 從 dry-run 推進到 gated send；預設仍 blocked。
- report 需列出 pending orders、partial fills、callback ordering issues、readiness blockers、manual actions。
- 保存 report artifact path / run id，方便隔日 regression correction。
- must-fix：alert 必須有 severity、dedupe key、send gate、owner / manual action、重送限制。
- must-fix：P9 report 必須分離 strategy evidence、execution health、operational readiness，避免一個總分誤導。
- 驗收：一個命令可產出 operator-ready report，並可在明確 gate 下發送摘要。

## P10: Regression Correction Loop

目前狀態：待處理。

目標：把每日 simulation ops 的錯誤、mismatch 與 missed cases 轉成可回歸修正的樣本。

- 匯入 daily ops artifacts，產生 regression cases。
- 將 failure 分類為 data issue、candidate quality、risk decision、broker/callback lifecycle、reporting issue。
- 自動產生 replay / simulation regression fixture。
- 修正後重跑對應 regression suite，避免同一錯誤重演。
- must-fix：每個 regression case 必須有 source run id、failure type、minimal fixture、expected behavior、closing test command。
- must-fix：每個 regression case 必須保存 raw source row / payload，不能只保存 normalized result。
- 驗收：每日問題能進 regression backlog，且可由 tests / smoke command 驗證已修正。

## P11: Live Execution Design

目前狀態：待處理。

目標：只有在 simulation ops 穩定後，才設計正式區下單，不在本 phase 之前啟用。

- 定義 live approval token 流程與人工操作 SOP。
- 設計 live broker adapter，但必須和 simulation gateway 分離。
- 設計權限控管、部位上限、daily stop、panic cancel、kill switch。
- 設計正式告警發送與人工確認回寫。
- must-fix：P11 entry criteria 必須包含連續 N 個 trading days simulation ops 無 blocker、無 unresolved partial fill / ordering issue、report 準時送達、regression backlog 無 P0/P1 open items。
- must-fix：simulation / readiness 只能證明執行鏈可控，不得當成 strategy edge 或正式交易獲利證明。
- must-fix：live adapter 必須自行驗證 readiness / approval / session / risk limits，不可只依賴 P7 report 的 `live_execution_allowed`。
- must-fix：正式 approval token 必須不可預測、短效、可稽核，不能使用日期格式預設 token。
- 驗收：即使 live adapter 存在，沒有正確 approval token / session gate / readiness ready 仍不能送單。

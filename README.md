# tw-day-trading-lab

台股當沖重建專案。這個 repo 的目標不是重寫舊 live bot，而是先建立能產生價值的研究與驗證閉環：

1. 產生隔日候選名單。
2. 驗證候選是否真的形成 `next_day_actionable`。
3. 用 replay / paper ledger 驗證策略樣本。
4. 只用 Shioaji simulation 驗證執行鏈路，不用模擬成交結果直接證明策略 edge。

舊專案 `quantitative-trading-decision-system` 只作為資料來源、失敗案例與 log archive。

## MVP 邊界

第一階段只做：

- `candidate-engine`
- `replay-harness`
- `paper-ledger`
- `shioaji simulation dry-run`
- `daily report`
- `Telegram summary dry-run`
- TiDB metadata + Parquet raw data layout

第一階段不做：

- 真實自動下單
- dashboard
- 自動參數最佳化上線
- 用 FinMind 做盤中即時進出場
- 用 Shioaji `kbars` 盤中掃全市場

## Execution Boundary

目前所有下單相關實作只允許使用 Shioaji simulation / fake SDK，用來完成開發與驗證。正式區登入、正式委託與自動下單不屬於本階段；任何真實 Shioaji smoke 都必須另加人工 gate。

## Quick Start

```bash
python3 -m unittest discover -s tests
python3 -m tw_day_trading_lab.cli candidates build \
  --date 2026-05-28 \
  --input examples/candidates.sample.json \
  --output reports/2026-05-28-candidates.json
python3 -m tw_day_trading_lab.cli report daily \
  --date 2026-05-28 \
  --input reports/2026-05-28-candidates.json \
  --format md \
  --output reports/2026-05-28-daily.md
python3 -m tw_day_trading_lab.cli old-logs import \
  --date 2026-03-25 \
  --input examples/old-log.sample.csv \
  --output reports/old-log-sample-samples.json \
  --report-output reports/old-log-sample-failure.md
python3 -m tw_day_trading_lab.cli db init --schema sql/001_init.sql
python3 -m tw_day_trading_lab.cli samples persist \
  --input reports/old-log-sample-samples.json
python3 -m tw_day_trading_lab.cli samples summary
python3 -m tw_day_trading_lab.cli ingest finmind \
  --date 2026-05-28 \
  --requests examples/finmind.requests.sample.json \
  --cache-dir data/raw
python3 -m tw_day_trading_lab.cli candidates build-from-raw \
  --date 2026-05-28 \
  --cache-dir data/raw \
  --output reports/2026-05-28-candidates-from-raw.json
python3 -m tw_day_trading_lab.cli replay samples \
  --date 2026-03-25 \
  --input reports/old-log-sample-samples.json \
  --output reports/2026-03-25-replay.json \
  --report-output reports/2026-03-25-replay.md \
  --cost-r 0.1
python3 -m tw_day_trading_lab.cli simulate run \
  --date 2026-05-28 \
  --input examples/simulation-plan.sample.json \
  --output reports/2026-05-28-simulation.json \
  --report-output reports/2026-05-28-simulation.md \
  --execution-sync-store reports/2026-05-28-execution-sync.json
python3 -m tw_day_trading_lab.cli simulate restart-sync \
  --store reports/2026-05-28-execution-sync.json \
  --output reports/2026-05-28-restart-sync.json
python3 -m tw_day_trading_lab.cli simulate ingest-callback \
  --date 2026-05-28 \
  --input examples/shioaji-callback.sample.json \
  --store reports/2026-05-28-execution-sync.json \
  --output reports/2026-05-28-callback-event.json
python3 -m tw_day_trading_lab.cli simulate callback-smoke \
  --date 2026-05-28 \
  --input examples/shioaji-callback-sequence.sample.json \
  --store reports/2026-05-28-callback-smoke-store.json \
  --output reports/2026-05-28-callback-smoke.json
python3 -m tw_day_trading_lab.cli report close \
  --date 2026-05-28 \
  --candidates reports/2026-05-28-candidates-from-raw.json \
  --replay reports/2026-03-25-replay.json \
  --simulation reports/2026-05-28-simulation.json \
  --output reports/2026-05-28-close.md \
  --telegram-summary-output reports/2026-05-28-telegram-summary.txt
python3 -m tw_day_trading_lab.cli notify telegram \
  --date 2026-05-28 \
  --report reports/2026-05-28-close.md \
  --dry-run
```

如果沒有安裝 package，先加上 `PYTHONPATH=src`：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build --date 2026-05-28 --input examples/candidates.sample.json
```

## Project Shape

```text
src/tw_day_trading_lab/
  candidate_engine.py   # 候選排序與 next_day_actionable 初版規則
  candidate_builder.py  # P4 raw JSONL -> candidate input / ranked candidates
  ledger.py             # paper / simulation idempotency 與部位生命週期骨架
  reports.py            # Markdown / HTML 報告與 daily close report
  replay.py             # P5 valid-only replay expectancy / R metrics
  simulation.py         # P6 Shioaji simulation dry-run adapter
  storage.py            # repository ports + TiDB/SQLite adapters
  finmind_ingestion.py  # P3 FinMind fetch ledger + raw cache ingestion
  cli.py                # MVP CLI
sql/
  001_init.sql          # TiDB schema
docs/
  rebuild-baseline.md   # 從舊專案整理出的重建基線
  data-contracts.md     # schema / sample contract
  mvp-roadmap.md        # 第一階段工作順序
  development-work.md   # 開發工作拆解與驗收標準
  old-log-importer.md   # P1 舊 log / CSV importer 說明
  tidb-integration.md   # P2 TiDB repository / CLI 說明
  finmind-ingestion.md  # P3 FinMind ingestion / cache 說明
  candidate-engine-v1.md # P4 raw cache 轉候選與資料缺口說明
```

## Current Decision

專案名稱採用 `tw-day-trading-lab`，因為第一階段定位是 lab：先快速證明候選、樣本與執行鏈路是否有價值，再決定是否拆出正式 live execution service。

預設分支使用 `master`。

## Why Import Old Logs First

舊 log / CSV importer 不是為了沿用舊策略，而是為了把舊系統的失敗資料轉成可驗證的反例資料。

第一階段先匯入舊 log 的價值：

- 立刻驗證 `valid / excluded / needs_review` 樣本分級是否能擋掉 debug、盤後測試、重複開倉與資料污染。
- 立刻重現同秒同標的雙 `ENTER` 問題，確認新 ledger 的 `idempotency_key` 能防住。
- 先用已知失敗樣本測報告與統計，避免新系統只在乾淨 sample 上看起來正常。
- 更快建立「不要再做什麼」的規則，這比一開始接新資料更快產生價值。

FinMind nightly ingestion 仍然必要，但它是下一步：用來建立新的候選資料來源，而不是用來回答舊系統到底壞在哪裡。

## Current Implementation Status

- P0-P7 已完成。
- 剩餘 phase 固定為 P8-P11：Simulation Ops Go-live、Ops Reports / Alerts、Regression Correction Loop、Live Execution Design。
- P8-P11 已完成 grill-me review 與全 phase re-check，最可能導致專案失敗的點已寫入 `docs/development-work.md` 與 `docs/mvp-roadmap.md`。
- Report Loop / Notification hardening 已完成：`report close` 可聚合 candidate / replay / simulation，列出逐筆 `needs_review` reason，並輸出專用 Telegram summary；真實發送仍維持 dry-run gate。
- Execution Sync MVP 已完成：dry-run simulation 可保存 broker trades / open positions 到 execution sync store，restart-sync 可重建 ledger open state 並比對 broker / ledger 是否一致。
- Shioaji callback normalization MVP 已完成：可將 callback payload 標準化為 execution callback event，寫入 execution sync store，並透過 restart-sync 暴露 broker / ledger mismatch。
- Shioaji order custom field contract 已完成：因 Shioaji SDK `custom_field` 最長 6 字元，order request 會把 `OrderIntent.idempotency_key` 轉成短 token，並用 execution sync store mapping 還原 callback intent。
- Shioaji SDK-shaped gateway MVP 已完成：可用 fake SDK 驗證 login / `api.Order` / `api.place_order` 邊界，並拒絕 `api.simulation=False`；真實登入與真實委託仍未啟用。
- Shioaji callback stream MVP 已完成：可註冊 `set_order_callback`，把 callback event normalize 後寫入 execution sync store，並拒絕 `api.simulation=False`。
- Execution lifecycle policy MVP 已完成：filled 確認開倉、cancelled / rejected 釋放 intent、partial fill 先進人工檢查，不自動改 ledger position。
- Duplicate callback dedupe MVP 已完成：相同 callback key 不會重複寫入 callback event、broker trade 或 lifecycle decision。
- Callback status ordering MVP 已完成：同一 broker order 若已記錄較後段狀態，後到的舊狀態會寫入 ordering issue 並跳過，不污染 callback event / broker trade / lifecycle decision。
- Execution sync store locking MVP 已完成：callback / result 寫入會用 file lock 包住 read-modify-write，並以 atomic replace 更新 JSON store，避免並發 callback 寫入造成 JSONDecodeError 或 lost write。
- Terminal-state policy MVP 已完成：同一 broker order 已進 `filled / cancelled / rejected` 後，若又收到不同終態，會寫入 `terminal_state_conflict` 並跳過，不當成正常 lifecycle progression。
- Longer callback smoke MVP 已完成：`simulate callback-smoke` 可把一串 callback payload 重播進 execution sync store，統計 accepted / skipped 與 ordering issues。
- Gated Shioaji simulation smoke guard 已完成：`simulate shioaji-smoke` 預設只輸出 blocked report，不 import Shioaji、不登入；必須明確帶 `--enable-login-smoke` 才會建立 `sj.Shioaji(simulation=True)` 並嘗試 simulation login。
- Gated callback stream smoke guard 已完成：必須先通過 login smoke 並帶 `--enable-callback-stream --store ...`，才會註冊 `set_order_callback`；此 smoke 仍不送單。
- Gated Shioaji simulation login 實測已通：`2026-06-02` 使用舊專案 env credential，`simulation_only=true`、`orders_allowed=false`、`fetch_contract=false`、`subscribe_trade=false`。
- Gated Shioaji callback registration 實測已通：`set_order_callback` 可註冊，store 初始 callback / broker trade / lifecycle decision 皆為 0。
- Gated Shioaji simulation order + cancel smoke 已完成：真實 `sj.Shioaji(simulation=True)` order / callback / cancel 鏈路已打通，最終 smoke summary total 4、ok 4、blocked 0；restart-sync matched 2、needs_review 0。
- P6 主線可收斂成四段：P6A 本地 simulation execution chain、P6B callback / restart-sync hardening、P6C gated login + callback registration、P6D gated simulation order + cancel smoke，四段皆已完成。
- P7 Production Readiness Gate 已完成：`simulate production-readiness` 會讀 execution sync store，檢查正式 live gate、交易時段、pending orders、partial fills、callback ordering、cancel retry plan，並輸出 JSON / Markdown readiness report。未提供明確人工 approval token 時，live execution 一律 blocked。
- P7 smoke 已用 P6 真實 simulation store 驗證：6 checks 中 5 ok、1 blocked；唯一 blocker 是 `formal_live_gate`，pending orders / partial fills / ordering issues 皆為 0。
- 下一步不是繼續拆 P7，也不是直接做正式下單；下一步是 P8 daily simulation ops go-live。
- P4b 已支援 FinMind 20-50 日窗口、`TaiwanStockInfo` 非普通股排除、法人 / 融資融券 enrichment 與缺資料降權。
- P5 已支援 classified samples replay，只用 `validity=valid` 計算 expectancy，並分開列示 gross / cost / net R。
- P6 已支援 `SignalIntent -> RiskDecision -> OrderIntent -> BrokerTrade -> LedgerPosition` dry-run，simulation sample 與 replay expectancy 分開，重送同一 intent 不會重複開倉。

## Remaining Phase Map

剩餘 4 個 phase，必須照順序做，不能把 simulation go-live、告警、回歸修正與正式下單設計混成一包。

| Phase | 目標 | 完成條件 |
|---|---|---|
| P8 Simulation Ops Go-live | 把 P6/P7 的 gated simulation chain 變成每日可執行的 simulation ops | 每日 run 可從 manifest replay / audit / explain |
| P9 Ops Reports / Alerts | 把 candidate / replay / simulation / restart-sync / readiness 合成 operator-ready report 與 gated alert | alert 有 severity、dedupe、owner、manual action、send gate |
| P10 Regression Correction Loop | 把每日 ops 問題轉成可重跑 regression fixture | 每個 case 有 source run id、raw payload、minimal fixture、expected behavior、closing test command |
| P11 Live Execution Design | 只在 simulation ops 穩定後設計正式區邊界 | live adapter 自行硬擋 approval / readiness / session / risk limits |

## Must-fix Before Next Coding

P8 開始實作前，先補三個硬契約：

- `plan_builder` contract：明確記錄 candidate source、risk decision reason、limit price source、quantity source、blocked reason。
- price / contract / session gates：送 simulation order 前檢查 limit up/down、reference price、contract loaded、regular session、trading day。
- `ops_run_manifest`：保存 run id、artifact path、input/output checksum、side effects summary、command、cwd、commit hash。

P8-P11 的核心邊界固定不變：simulation / readiness 只能證明執行鏈可控，不能當成 strategy edge 或獲利證明。

## Documentation Map

- `docs/development-work.md`：開發歷程、驗證命令、P8-P11 grill-me review、全 phase failure review。
- `docs/mvp-roadmap.md`：目前 phase 狀態與後續 phase 驗收條件。
- `docs/data-contracts.md`：candidate / replay / simulation / execution sync / readiness / ops manifest 契約。
- `docs/rebuild-baseline.md`：舊專案重建基線。
- `docs/old-log-importer.md`、`docs/tidb-integration.md`、`docs/finmind-ingestion.md`、`docs/candidate-engine-v1.md`：各子系統說明。

# tw-day-trading-lab

台股當沖重建專案。這個 repo 的目標不是重寫舊 live bot，而是先建立能產生價值的研究與驗證閉環：

1. 產生隔日候選名單。
2. 驗證候選是否真的形成 `next_day_actionable`。
3. 用 replay / paper ledger 驗證策略樣本。
4. 只用 Shioaji simulation 驗證執行鏈路，不用模擬成交結果直接證明策略 edge。

舊專案 `quantitative-trading-decision-system` 只作為資料來源、失敗案例與 log archive。

## MVP 邊界

第一階段定位是 lab，不是正式 live bot。已完成的能力包含：

- `candidate-engine`
- `replay-harness`
- `paper-ledger`
- `shioaji simulation ops-run`
- `daily report`
- `Telegram summary / gated alerts`
- TiDB metadata + raw data cache
- regression correction loop
- live execution boundary design

第一階段不做：

- 真實自動下單
- 自動參數最佳化上線
- 用 FinMind 做盤中即時進出場
- 用 Shioaji `kbars` 盤中掃全市場

## Execution Boundary

目前所有日常下單驗證只允許使用 Shioaji simulation / fake SDK。repo 內已有 live execution adapter boundary，但正式區登入、正式委託與自動下單仍預設 blocked；任何真實 Shioaji smoke 都必須另加人工 gate，且不得使用 `sj.Shioaji(simulation=False)` 做隨手測試。

## Target Operating Cycle

本專案的最終目標是「交易日自動循環」，不是只產生單次 simulation bundle。

目標流程：

1. 17:30 產生下一個交易日候選股名單。
2. 交易日 09:05 或 10:00 啟動候選股監控。
3. 09:05/10:00-13:20 期間，反覆檢查候選股是否符合進場 / 出場策略，通過風控後才執行。
4. 13:20 停止新進場，執行必要的 time stop / force-exit / cancel policy。
5. 等待收盤後資料沉澱。
6. 15:00 產出當日交易結果、優缺點、blocker 與 next actions，發布到 GitHub 並回傳連結。
7. 17:30 整理明日候選名單，進入下一個交易日循環。

目前 `simulate daily-ops` / `make daily-ops` 是重要基礎，但它只是「單日 bundle runner + audit」，還不是完整的 time-aware intraday trading loop。完整目標與校正後 phase 請看 `docs/trading-day-autonomous-cycle.md`。

## Quick Start

### Python / Shioaji runtime

Shioaji 精確鎖定為 `1.7.5`，並以 repo 內的 `.venv` 隔離驗證：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -c 'import shioaji; print(shioaji.__version__)'
```

執行 Shioaji 相關命令時使用 `.venv/bin/python`。安裝本身不會啟用登入、行情訂閱或委託。

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src tests
python3 -m tw_day_trading_lab.cli db migrate --schema-dir sql
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
python3 -m tw_day_trading_lab.cli simulate ops-run \
  --date 2026-06-04 \
  --candidates-input reports/2026-06-04-candidates.json \
  --allow-outside-session
python3 -m tw_day_trading_lab.cli simulate regression-import \
  --manifest reports/2026-06-04-ops/ops_run_manifest.json
python3 -m tw_day_trading_lab.cli simulate daily-ops \
  --date 2026-06-04 \
  --skip-ingestion \
  --allow-outside-session \
  --fail-on-audit
python3 -m tw_day_trading_lab.cli simulate trading-day-cycle \
  --date 2026-06-04 \
  --trading-data-input data/raw/finmind/TaiwanStockPrice/2026-06-04/0050.jsonl \
  --run-all-stages
```

Sector Flow V1（族群資金流）已於 2026-10-03 搬到 `~/services/stock/tw-day-trading`（MVP commit 3d6032e），本 repo 不再包含。

### 一鍵執行當沖模擬 (Simplified Daily Ops Run)

重構後的程式支持自動載入 `.env` 與憑證，並提供極簡的 Makefile 命令進行模擬下單交易測試：

```bash
# 預設執行：自動使用當前日期、自動尋找候選檔、讀取環境變數登入模擬平台並載入 Sinopac.pfx 憑證下單
make ops-run

# 自訂附加參數 (例如於非交易時段強制執行)
make ops-run ARGS="--allow-outside-session"
```

### 每日營運鏈 (Daily Simulation Ops Automation)

`Daily Simulation Ops Automation v1` 已提供上層 orchestration，將 ingestion、candidate build、`simulate ops-run`、close report、alerts、regression import 與 bundle audit 串成單一命令。此命令預設忽略 `IS_SIMULATION` 環境變數，不會因環境殘留而登入 Shioaji；只有明確帶 `--simulation-on` 才會走 simulation broker side effect。

```bash
# 完整每日鏈，預設會為當日產生 FinMind ingestion requests；alert 預設 dry-run
make daily-ops DATE=2026-06-04

# 本地 fixture / 無 token 驗證，可跳過 ingestion 並強制 audit 失敗時回傳非 0
make daily-ops DATE=2026-06-04 ARGS="--skip-ingestion --allow-outside-session --fail-on-audit"

# 只有明確允許時才真實發送 alert
make daily-ops DATE=2026-06-04 ARGS="--send-alerts"
```

預設輸出在 `reports/{date}-daily-ops/`：

- `candidates.json`
- `ops/ops_run_manifest.json`
- `ops/alerts.json`
- `ops/readiness_report.json`
- `ops/simulation_output.json`
- `close.md`
- `telegram-summary.txt`
- `regression/`
- `daily_bundle_audit.json`

### 交易日循環狀態機 (Trading Day Cycle Dry Run)

`simulate trading-day-cycle` 已提供交易日循環 dry-run 狀態機。它不查額外交易日曆；交易日判定只看 API / raw cache 是否有當日交易資料。若指定資料或預設 raw cache 中找不到任何交易 rows，會輸出 `calendar_status=non_trading_day` 並停在 `blocked`，不進入盤中流程。

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle \
  --date 2026-06-04 \
  --trading-data-input data/raw/finmind/TaiwanStockPrice/2026-06-04/0050.jsonl \
  --candidates-input reports/2026-06-04-candidates.json \
  --intraday-bars-input fixtures/2026-06-04-intraday-bars.json \
  --position-state-input reports/2026-06-04-trading-day-cycle/position_state.json \
  --current-time 09:20 \
  --start-policy 09:05 \
  --run-all-stages
```

預設輸出在 `reports/{date}-trading-day-cycle/trading_day_run_state.json`，目前會記錄：

- `calendar_status` / `calendar_rule=api_data_availability_only`
- 09:05 或 10:00 start policy
- 13:20 hard stop
- 14:00 close buffer end
- 15:00 report stage
- 17:30 next candidates stage
- idempotency key / lock policy / retry policy
- candidate artifact / trading data probe / watch events / order intents / position state / report artifact / next-candidate artifact / end-to-end smoke artifact

若提供 `--candidates-input`，同一輪也會產出 `watch_events.json`。watch loop 只監控候選名單，不盤中掃全市場；沒有持倉的候選會走 `VwapBreakoutStrategy` entry dry-run，已有持倉的標的會先走 exit dry-run。13:20 之後的新 entry 一律輸出 `entry_rejected` / `after_hard_stop`。盤中 bars 可以用 `--intraday-bars-input` 指定 fixture，也可以不指定，改由 raw cache adapter 依候選股讀取 `data/raw/finmind/TaiwanStockPriceMinute/{date}/{symbol}.jsonl`。

同一輪會接著產出 `order_intents.json`、`position_state.json`、`report.md`、`next_candidates.json` 與 `end_to_end_smoke.json`。這些仍是 dry-run artifact：duplicate intent 會被 suppress、13:20 後 stale pending order 會產生 cancel intent、15:00 report 只做 local / would-send、17:30 next candidates 不猜下一交易日，只標記 `next_api_available_trading_day`。

多日穩定性 smoke 可用：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle-smoke \
  --dates 2026-06-04,2026-06-05,2026-06-06 \
  --cache-dir data/raw \
  --candidates-input-pattern 'reports/{date}-candidates.json' \
  --run-all-stages
```

它會逐日執行完整 dry-run cycle，輸出 `reports/trading-day-cycle-smoke/multi_day_smoke_summary.json` 與 `multi_day_smoke.md`。有 API / raw cache 交易 rows 的日期才會進入 artifact-chain 檢查；取不到交易資料的日期會被標成 `skipped_non_trading_day`，不會查假日日曆。

真實 raw-cache observation 應加上 `--require-intraday-bars`，要求每個候選股都有 candidate-scoped intraday rows；若只有日線交易 probe、缺候選股分 K，該日期會標成 `blocked` / `required_intraday_bars_missing`，避免把「artifact chain 可產出」誤讀成「真實盤中資料已可用」。

### 即時行情 Tick 訂閱 (Price Action P1, gated)

`simulate shioaji-tick-smoke` 預設 blocked：不 import Shioaji、不登入、不訂閱，只輸出 `enable_tick_stream_required` report。

```bash
# 預設 dry gate，確認 side_effects 為空
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-tick-smoke \
  --date 2026-08-14 \
  --candidates-input reports/2026-08-14-candidates.json

# 明確開 gate 才會登入 simulation 並訂閱候選股 tick
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-tick-smoke \
  --date 2026-08-14 \
  --candidates-input reports/2026-08-14-candidates.json \
  --duration-seconds 60 \
  --enable-tick-stream
```

開 gate 後只登入行情（`fetch_contract=True`、`subscribe_trade=False`），不碰委託鏈路、不送單、不取消。訂閱範圍限定候選名單，不掃全市場。raw tick 全部寫入 `data/raw/shioaji/ticks/{date}/{symbol}.jsonl`，包含被拒絕的 `simtrade` / 盤中零股 rows，供 audit。

### 由 Tick 重建 1m K 棒 (Price Action P2)

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli bars build \
  --date 2026-08-17 \
  --symbols 2330 \
  --cache-dir data/raw
```

從 P1 的 raw tick artifact 重建 canonical 1m bar，輸出 `reports/{date}-bars.json`（含 `aggregator` 統計與 `volume_check`）。replay 套用與 live stream 相同的過濾、sequence 與 dedupe 規則，因此重播一場 session 會得到與當時一模一樣的 MarketTick 與 bar。

如果沒有安裝 package，先加上 `PYTHONPATH=src`：

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli candidates build --date 2026-05-28 --input examples/candidates.sample.json
```

## Project Shape

```text
src/tw_day_trading_lab/
  candidate_engine.py   # 候選排序與 next_day_actionable 初版規則
  candidate_builder.py  # P4 raw JSONL -> candidate input / ranked candidates
  cost.py               # 台股當沖成本與 tick size 滑價模型
  ledger.py             # paper / simulation idempotency 與部位生命週期骨架
  live.py               # live execution adapter boundary / approval token gate
  market_data.py        # Price Action P1 Shioaji tick -> normalized MarketTick
  bars.py               # Price Action P2/P3/P4 canonical 1m / 5m + bar event store
  rvol.py               # Price Action P5 TOD-RVOL / Cumulative RVOL
  backfill.py           # Price Action P5 gated Shioaji kbars historical backfill
  structure.py          # Price Action P6 swing / HH-HL-LH-LL / BOS
  setup.py              # Price Action P7 breakout -> retest -> trigger state machine
  paper.py              # Price Action P8 paper trading daily cycle
  market_regime.py      # 0050 proxy 大盤環境過濾
  performance.py        # rolling expectancy / drawdown feedback
  reports.py            # Markdown / HTML 報告與 daily close report
  replay.py             # P5 valid-only replay expectancy / R metrics
  simulation.py         # P6 Shioaji simulation dry-run adapter
  strategy.py           # intraday signal / VWAP breakout strategy components
  storage.py            # repository ports + TiDB/SQLite adapters
  finmind_ingestion.py  # P3 FinMind fetch ledger + raw cache ingestion
  cli.py                # MVP CLI
sql/
  001_init.sql          # TiDB schema
  002_schema_version.sql # migration version tracking
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

- P0-P11 已完成。
- Phase B 已完成：成本 / 滑價、風控與出場、微結構 gate、回測方法、績效回饋、策略 edge redesign。
- Post-market hardening Sprint 3-A/B/C 已完成：ADV-20d / 張數流動性、ops-run git / lock pre-flight、0050 market regime filter。
- Sprint 4-A/B/C 已完成：Telegram 真實發送 gate、live approval HMAC token、TiDB migration strategy。
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
- P8 Simulation Ops Go-live 已完成：`simulate ops-run` 可產生 input plan、simulation output、callback store、restart-sync、readiness report、alerts 與 `ops_run_manifest`。
- P9-P11 infrastructure 已完成：alerts、regression import/run、live adapter boundary。
- 最新安全維護：`.pfx` / `.p12` 已加入 `.gitignore`，`Sinopac.pfx` 已從可達 Git history 移除。
- `Daily Simulation Ops Automation v1` 前兩步已完成：`simulate daily-ops` / `make daily-ops` 可啟動每日作業鏈，`daily_bundle_audit.json` 可檢查 manifest、artifact checksum、readiness、alerts operator fields、close report 與 regression case traceability。
- Trading Day Autonomous Cycle v1 第 1-8 步已完成 dry-run 版：`simulate trading-day-cycle` 可產出 run state、watch events、order intents、position state、15:00 report、17:30 next candidates handoff 與 end-to-end smoke。交易日不用額外日曆判斷；取不到交易資料即 `non_trading_day`。
- Simulate online-test preparation 已開始：`simulate trading-day-cycle` 可用 candidate-scoped raw-cache intraday adapter 取代 fixture bars，`simulate trading-day-cycle-smoke` 可連跑多日並輸出 stability summary。Shioaji side effect、GitHub publish、Telegram send 仍維持 disabled / dry-run gate。
- Price Action Intraday PA-P1 已完成：`market_data.py` 提供 `MarketTick`、`normalize_shioaji_tick`、`ShioajiTickStream` 與 `data/raw/shioaji/ticks/{date}/{symbol}.jsonl` raw store。`simtrade` / 盤中零股 / 暫停交易 tick 一律拒絕，成交量統一換算成股。strategy 層不依賴 Shioaji SDK，`market_data.py` 也不 import SDK。當時 PA-P2 tick → 1m 尚未實作。
- PA-P1 code review 修正已完成：provider callback 只入佇列不做 I/O（避免卡住行情 feed），壞掉的 `volume` 一律 `needs_review` 不再靜默轉成 0，ingestion 端二次檢查 candidate scope，duplicate tick 以 `(symbol, timestamp, cumulative_volume)` 去重，`code` 缺失的 tick 保存到 `_unknown.jsonl`，subscribe 例外與觀察窗中斷都保證 unsubscribe / logout。
- PA-P1 market data health 已完成：`dropped_queue_full` / `worker_errors` / `raw_write_errors` / `sink_errors` / `worker_failed` / `worker_stop_timeout` 皆可觀察，聚合成 `HEALTHY` / `DEGRADED` / `FAILED`。系統不保證永不掉 tick，但掉了一定知道；health 非 `HEALTHY` 時 smoke fail closed 並回傳非 0，下游禁止產生新進場訊號。另有逐檔 volume sanity check 交叉驗證 `cumulative_volume` 差值與 `trade_volume` 加總。
- Price Action Intraday PA-P2 已達 Gate A `PA-P2_CODE_COMPLETE`：`bars.py` 提供 `MarketBar` 與 `OneMinuteBarAggregator`，半開 bucket `[09:00:00, 09:01:00)`、事件時間 watermark、多股票各自狀態、late tick 窗內併入／窗外 `CORRECTED` rev+1／超窗 `dropped_late`、sequence gap 逐 bar 標記。**沒有成交的分鐘不補 synthetic bar**，以保持「真的量 0 / 沒人交易 / feed 漏資料」三者可分辨。`bars build` 可從 PA-P1 raw tick 重建 1m bar，replay 與 live 共用同一套 aggregator。Gate B `PA-P2_LIVE_VALIDATED` 已於 2026-08-17 達成：真實 tick 聚合的 1m 與 Shioaji kbars 比對，volume ratio 0.997–1.000、雙方無獨有分鐘。詳見 `docs/price-action-p2-design.md`。
- Price Action Intraday PA-P3 已達 Gate A `PA-P3_CODE_COMPLETE`：`FiveMinuteBarAggregator` 由 canonical 1m 聚合出 canonical 5m，bucket `[09:00, 09:05)`。**5m 永遠不向 provider 取得**，只接受 `timeframe="1m"` 輸入。bucket 是時間區間不是「五根 1m」，缺分鐘照樣產生 5m 且不補假資料、不借下一個 bucket 湊數。1m correction 會 **replace 該分鐘並重算整根 5m**（不是加總），發出 `CORRECTED` 且 revision +1，舊 revision 物件不變。Gate B `PA-P3_LIVE_VALIDATED` 已於 2026-08-17 達成。詳見 `docs/price-action-p3-design.md`。
- Price Action Intraday PA-P4~P8 已達 Gate A：`bars.py` 的 append-only bar event store、`rvol.py` 的 TOD-RVOL / Cum-RVOL、`backfill.py` 的 gated Shioaji kbars 盤前 backfill、`structure.py` 的 swing 與市場結構、`setup.py` 的 Breakout → Retest → Trigger 狀態機、`paper.py` 的 paper trading 日循環。**RVOL 缺失或樣本不足一律不通過 volume gate**、**同一根 bar 內先判 stop 再判 target**、**market data 不健康時禁止新進場**、**收盤前一定清倉**。PA-P5 Gate B 於 2026-08-17 達成，PA-P7 / PA-P8 Gate B 於 2026-08-19 達成。Gate B 只證明機制會動，paper trade 結果仍只證明 determinism 與規則遵循，不是 strategy edge。
- Price Action Intraday PA-P9 已完成（2026-08-19）：成本進入 R（`cost_r` / `net_r` / `risk_ticks`），`summarize_expectancy` 輸出 expectancy / win rate / PF / MDD，毛淨並列；進場前成本閘門 `DEFAULT_MAX_COST_R = 1.0`；ETF 成本另計；研究標的定版為 `config/universe.txt` 37 檔。
- **PA 研究結論（2026-08-21～08-22）：目前沒有 edge。** 突破訊號 alpha 約為零；37 檔有持續盤中下飄而引擎寫死只做多；無條件做空 + 全掛單仍為淨 -0.066 R。13:25 強制平倉是原則，不留倉。詳見 `docs/development-work.md`。
- Shioaji 1.7.5 離線升級已完成（2026-10-02）：`shioaji_compat.py` 集中登入、合約查找與 enum 相容層；交易時段的真實 simulation smoke 尚未跑。
- Sector Flow V1（族群資金流）已於 2026-10-03 搬到 `~/services/stock/tw-day-trading`（MVP commit 3d6032e），本 repo 不再包含。
- **PA-P1 驗收分兩段**：Gate A `PA-P1_CODE_COMPLETE` 已達成（50 個 unit / adversarial tests）；Gate B `PA-P1_LIVE_VALIDATED` 已於 2026-08-17 達成（交易時段 smoke `live_validation.passed=true`），08-19～08-21 三個完整場次 12 檔亦通過。smoke report 的 `live_validation.passed` 會自己算出有沒有達標，不需人工核對欄位。詳見 `docs/price-action-p1-design.md`。
- P4b 已支援 FinMind 20-50 日窗口、`TaiwanStockInfo` 非普通股排除、法人 / 融資融券 enrichment 與缺資料降權。
- P5 已支援 classified samples replay，只用 `validity=valid` 計算 expectancy，並分開列示 gross / cost / net R。
- P6 已支援 `SignalIntent -> RiskDecision -> OrderIntent -> BrokerTrade -> LedgerPosition` dry-run，simulation sample 與 replay expectancy 分開，重送同一 intent 不會重複開倉。

## Completed Phase Map

| Phase | 目標 | 狀態 |
|---|---|---|
| P8 Simulation Ops Go-live | 把 P6/P7 的 gated simulation chain 變成每日可執行的 simulation ops | 已完成 |
| P9 Ops Reports / Alerts | 把 candidate / replay / simulation / restart-sync / readiness 合成 operator-ready report 與 gated alert | 已完成 |
| P10 Regression Correction Loop | 把每日 ops 問題轉成可重跑 regression fixture | 已完成 |
| P11 Live Execution Design | 只在 simulation ops 穩定後設計正式區邊界 | 已完成 |
| Phase B | 修正成本、風控、微結構、回測、績效回饋與策略 edge 六大問題 | 已完成 |
| Sprint 3-A/B/C | 流動性、ops pre-flight、market regime hardening | 已完成 |
| Sprint 4-A/B/C | Telegram gate、HMAC approval、TiDB migration | 已完成 |
| Trading Day Autonomous Cycle v1 | 交易日 scheduler / intraday watch loop / 13:20 force-exit / 15:00 GitHub report / 17:30 next candidates | 第 1-8 步已完成 dry-run；side-effect gate 未做 |
| PA-P1 Shioaji Tick → MarketTick | provider adapter、normalization、market data health、gated tick smoke | Gate A / Gate B 完成（2026-08-17） |
| PA-P2 MarketTick → canonical 1m | 固定 bucket、OHLCV、late tick、不補 synthetic、replay | Gate A / Gate B 完成（2026-08-17） |
| PA-P3 canonical 1m → canonical 5m | 固定 bucket、correction replace 重算、deterministic replay | Gate A / Gate B 完成（2026-08-17） |
| PA-P4 Bar persistence | append-only bar event store | 完成（無 Gate B） |
| PA-P5 TOD-RVOL / kbars backfill | time-slot baseline、gated 盤前 backfill | Gate A / Gate B 完成（2026-08-17） |
| PA-P6/P7/P8 結構 / setup / paper | swing、breakout → retest → trigger、paper trading 日循環 | Gate A 完成；P7/P8 Gate B 完成（2026-08-19） |
| PA-P9 成本 / expectancy / ablation | 成本進 R、expectancy / PF / MDD、成本閘門 | 已完成；結論為目前無 edge |
| Shioaji 1.7.5 upgrade | 版本鎖定與 SDK 相容層 | 離線完成；真實 smoke 未跑 |

`PA-Pn` 是 `docs/price-action-intraday-plan.md` 的階段編號，與上表的 P8-P11 是兩套不同編號。

## Next Development Priority

目前有三條線，不要混在一起。

### Track 1：Price Action Intraday（進行中，研究階段）

PA-P1～PA-P9 程式皆完成，資料鏈與 paper 鏈已通過真實行情驗證。**阻斷已不是 plumbing，而是沒有 edge。** 依 2026-08-22 的方向依序：

1. 引擎改雙向或做空（`paper.py` / `setup.py` 目前只找做多突破）。進入做空 simulation 前，先查證先賣後買當沖資格與平盤下放空豁免，兩者都未查證。
2. 改用限價掛單（約省 39% 成本）；掛單 0 滑價是樂觀上界，需實測成交率與逆選擇代價。
3. 在 1、2 之上找剩下約 0.13% 的 alpha。

另外：依賴 Shioaji 1.7.5 收行情前，用 `.venv/bin/python` 在交易時段跑一次 gated simulation tick smoke（repo-local `.venv` 已於 2026-10-03 建立；系統 `python3` 仍是 1.3.2）。

### Track 2：Trading Day Autonomous Cycle v1（暫停，仍有效）

第 1-8 步已完成 dry-run 版。剩下的是：

- 另開 explicit gate 接 Shioaji simulation login/order/cancel side effects，預設維持 disabled。
- 另開 explicit gate 接真實 GitHub publish / Telegram operator link send。
- 正式 live order 繼續 blocked，直到另有 production approval SOP。

核心邊界固定不變：simulation / readiness 只能證明執行鏈可控，不能當成 strategy edge 或獲利證明；smoke OK 也不等於 live-order readiness。

Sector Flow V1（族群資金流）已於 2026-10-03 搬到 `~/services/stock/tw-day-trading`（MVP commit 3d6032e），本 repo 不再包含。

## Documentation Map

- `docs/development-work.md`：開發歷程、驗證命令、P8-P11 / Phase B / hardening sprint close-out。
- `docs/mvp-roadmap.md`：目前 phase 狀態與後續 phase 驗收條件。
- `docs/data-contracts.md`：candidate / replay / simulation / execution sync / readiness / ops manifest 契約。
- `docs/trading-day-autonomous-cycle.md`：校正後的交易日自動循環目標、schedule、架構差距與下一步 phase。
- `docs/rebuild-baseline.md`：舊專案重建基線。
- `docs/six-problems-review-and-ops.md`：六大問題修正與自動化營運共識。
- `docs/price-action-intraday-plan.md`：Price Action 策略計畫。**其 P1-P9 編號與本 repo 的 P0-P11 是兩套不同編號**，文件與 commit 一律用 `PA-Pn` 指涉前者。
- `docs/price-action-p1-design.md`：PA-P1 tick adapter 設計、兩段驗收與 live smoke 證據。
- `docs/price-action-p2-design.md`：PA-P2 1m 聚合，含「canonical layer 不補 synthetic bar」的理由。
- `docs/price-action-p3-design.md`：PA-P3 5m 聚合與 1m correction 規則。
- `docs/price-action-p4-design.md`：PA-P4 append-only bar event store。
- `docs/price-action-p5-design.md`：PA-P5 RVOL、Shioaji kbars backfill 與 FinMind 分 K 付費限制。
- `docs/price-action-p6-p8-design.md`：PA-P6 結構、PA-P7 setup 狀態機、PA-P8 paper trading。
- `docs/superpowers/specs/2026-10-02-shioaji-1-7-5-upgrade-design.md`：Shioaji 1.7.5 相容層設計。
- `docs/old-log-importer.md`、`docs/tidb-integration.md`、`docs/finmind-ingestion.md`、`docs/candidate-engine-v1.md`：各子系統說明。

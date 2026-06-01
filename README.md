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

- P0-P6 MVP 已完成。
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
- 下一步是 gated Shioaji simulation smoke。
- P4b 已支援 FinMind 20-50 日窗口、`TaiwanStockInfo` 非普通股排除、法人 / 融資融券 enrichment 與缺資料降權。
- P5 已支援 classified samples replay，只用 `validity=valid` 計算 expectancy，並分開列示 gross / cost / net R。
- P6 已支援 `SignalIntent -> RiskDecision -> OrderIntent -> BrokerTrade -> LedgerPosition` dry-run，simulation sample 與 replay expectancy 分開，重送同一 intent 不會重複開倉。

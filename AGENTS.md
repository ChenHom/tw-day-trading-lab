# AGENTS.md - tw-day-trading-lab

> **本資料夾**：這個資料夾是台股**當沖**重建實驗室，只有 CLI、沒有站台。`~/services/stock/tw-day-trading` 是另一個專案（波段系統，GitHub `tw-swing-trading-mvp`），名稱相近但無關。詳見文末「台股相關資料夾對照」。

This repo is the clean rebuild lab for Taiwan stock day-trading research, replay, reporting, and Shioaji simulation execution-chain validation.

## Project Mission

Do not treat this repo as the old live bot. The goal is to build a reliable, auditable workflow before any live execution work:

1. Build next-day candidate lists.
2. Validate whether candidates became `next_day_actionable`.
3. Use replay / paper ledger to evaluate strategy samples.
4. Use Shioaji simulation only to validate execution-chain behavior.
5. Keep strategy evidence, execution health, and operational readiness separate.

The old repo `~/services/stock/quantitative-trading-decision-system` is only a data source, log archive, and failure-case reference.

## Hard Boundaries

- Do not enable formal live trading.
- Do not log in with `sj.Shioaji(simulation=False)`.
- Do not place real live orders.
- Do not cancel real live orders.
- Do not treat Shioaji simulation results as strategy expectancy or profitability proof.
- Do not print API keys, secrets, CA passwords, approval tokens, or `.env` values.
- Any real Shioaji smoke must be explicitly gated and must use `sj.Shioaji(simulation=True)`.
- `ShioajiSdkSimulationGateway` and callback stream code must continue to reject `api.simulation=False`.
- `ShioajiTickStream` and any future quote/market-data stream must also reject `api.simulation=False`, and must subscribe only candidate-scoped symbols.

If a task appears to require live execution, stop and design the gate/SOP first. Do not "just test it".

## Current Status

- P0-P11 are complete.
- Phase B is complete: realistic cost / slippage, risk and exit engine, market microstructure gates, backtest methodology upgrades, rolling performance feedback, and strategy edge redesign.
- Sprint 3-A/B/C are complete: ADV-20d liquidity filter, ops-run git / lock pre-flight checks, and 0050 market regime filter.
- Sprint 4-A/B/C are complete: Telegram send gate, HMAC live approval token, and TiDB migration strategy.
- Report Loop / Notification hardening is complete, with Telegram still gated by environment and dry-run controls.
- P6 is complete as:
  - P6A local simulation execution chain.
  - P6B callback / restart-sync hardening.
  - P6C gated simulation login + callback registration.
  - P6D gated simulation order + cancel smoke.
- P7 Production Readiness Gate is complete as a report contract.
- P8 Simulation Ops Go-live is complete with `simulate ops-run`, `ops_run_manifest`, pre-order gates, input plan, readiness, alerts, and regression traceability.
- P9-P11 infrastructure is complete: operator alerts, regression import/run, and live broker adapter boundary.
- Daily Simulation Ops Automation v1 first two implementation steps are complete: `simulate daily-ops` / `make daily-ops` orchestration and `daily_bundle_audit.json`.
- Direction correction is complete: the project target is a full trading-day autonomous cycle, not only a single daily simulation bundle.
- Trading Day Autonomous Cycle v1 steps 1-8 are complete as dry-run artifacts: `simulate trading-day-cycle` emits run state, watch events, order intents, position state, 15:00 report, 17:30 next-candidate handoff, and end-to-end smoke. Trading-day status is based only on API/raw-cache data availability. Do not add a separate holiday calendar unless explicitly requested.
- Simulate online-test preparation is in progress: `simulate trading-day-cycle` can read candidate-scoped intraday bars from raw cache when no fixture is supplied, and `simulate trading-day-cycle-smoke` can run multi-day stability checks. Keep Shioaji side effects, GitHub publish, and Telegram send disabled unless a later task explicitly opens those gates.
- `.pfx` / `.p12` files are ignored, and `Sinopac.pfx` has been removed from reachable Git history.
- Price Action Intraday PA-P1 acceptance is split into two gates in `docs/price-action-p1-design.md`. Gate A `PA-P1_CODE_COMPLETE` is reached: `market_data.py` provides `MarketTick`, `normalize_shioaji_tick`, `ShioajiTickStream`, the `data/raw/shioaji/ticks/` raw store, market-data health, and the gated `simulate shioaji-tick-smoke`. Gate B `PA-P1_LIVE_VALIDATED` is reached: the 2026-08-17 trading-hours smoke reported `live_validation.passed=true`, and the 2026-08-19 / 08-20 / 08-21 full sessions on 12 symbols also passed with every loss counter at 0.
- Price Action Intraday PA-P2 reached Gate A `PA-P2_CODE_COMPLETE`: `bars.py` provides `MarketBar`, `OneMinuteBarAggregator`, `aggregate_ticks`, `latest_bars`, `check_bar_volume`, plus `market_data.replay_raw_ticks` and the `bars build` CLI. Gate B `PA-P2_LIVE_VALIDATED` is reached (2026-08-17): tick-built 1m matched Shioaji kbars on the overlapping minutes, with volume ratios of 0.997-1.000 and no minutes unique to either side once kbars were labelled by bar end. The comparison provider is Shioaji kbars, not FinMind. See `docs/price-action-p2-design.md`.
- Canonical 1m has NO synthetic bars: a minute with no trades produces no `MarketBar`. This deliberately overrides the Missing Minute Policy in `docs/price-action-intraday-plan.md`, so "real zero volume", "nobody traded" and "the feed dropped data" stay distinguishable. Do not add `is_synthetic` back at the canonical layer.
- Price Action Intraday PA-P3 reached Gate A `PA-P3_CODE_COMPLETE`: `bars.FiveMinuteBarAggregator` / `aggregate_1m_to_5m` build canonical 5m from canonical 1m. Gate B `PA-P3_LIVE_VALIDATED` is reached (2026-08-17). The provider places the 13:30 closing auction in its 13:25 5m bucket and we place it at 13:30; the volumes are equal, so this is a labelling difference and not a data difference. See `docs/price-action-p3-design.md`.
- 5m is never taken from a provider and never from another 5m: `FiveMinuteBarAggregator.on_bar` accepts only `timeframe == "1m"`. A 5m bucket is a time range, not five bars, and a corrected 1m replaces its minute and forces a full bucket recompute - it is never added to the previous revision.
- `bars.py` must stay provider-agnostic: its only project import is `MarketTick`. Both aggregators advance on event time only, never the wall clock, so replay and live produce identical bars.
- Price Action Intraday PA-P4 is complete: `bars.append_bars` / `read_bar_events` / `load_latest_bars` / `load_bar_revision` persist bars to an append-only JSONL event store at `data/bars/{timeframe}/{date}/{symbol}.jsonl`. Timeframe is a directory level so 1m and 5m can never collide. A correction is a new line, never an overwrite. PA-P4 has no Gate B. See `docs/price-action-p4-design.md`.
- Price Action Intraday PA-P5 reached Gate A `PA-P5_CODE_COMPLETE`: `rvol.py` builds per-time-slot volume baselines and computes TOD-RVOL / Cum-RVOL; `backfill.py` fetches historical 1m via the gated Shioaji kbars backfill. Gate B `PA-P5_LIVE_VALIDATED` is reached (2026-08-17): `baseline_days=20`, and a hand-computed slot median and TOD-RVOL matched the code exactly. See `docs/price-action-p5-design.md`.
- FinMind minute K is NOT available: `TaiwanStockKBar` returns HTTP 400 `Your level is free. Please update your user level.` (verified 2026-08-16 against the live API). Daily `TaiwanStockPrice` is unaffected, works anonymously, and its `Trading_Volume` is confirmed to be in shares. The RVOL baseline therefore comes from Shioaji kbars, not FinMind.
- `bars backfill-kbars` is a pre-market, bounded, candidate-scoped historical fetch, gated off by default. This is not the intraday kbars polling AGENTS.md forbids. It logs in for market data only and refuses `simulation != True`.
- Shioaji kbar `ts` encodes exchange local time as a naive UTC epoch. Decode it with `tz=timezone.utc` and drop the tzinfo. Using the host's local timezone shifts every bar by the UTC offset (09:01 becomes 17:01 on a UTC+8 host) and gives different answers on different machines, which would break every RVOL time slot.
- The Shioaji kbar volume unit is lots: the official 2330 sample (Volume 2565, close ~2230, Amount ~5.7 billion) only reconciles if 2565 counts lots. Backfill converts to shares. `check_backfill_against_daily` settled it on 2026-08-17 with ratios of 0.89-0.96; a ratio near 1000 or 0.001 means the conversion is wrong. Never pass `--volume-in-shares` to a Shioaji kbars backfill: it would shrink the baseline 1000x and make every bar look like a volume breakout.
- Shioaji kbars accept at most 30 days per request; `backfill.py` fetches longer ranges in chunks inside one login. The daily data quota is 500 MB (`api.usage()`). History from 2024-01-02 is backfilled for the research universe.
- An RVOL result with fewer than `min_days` samples returns `status=insufficient_data` and NO ratio. Do not change it to return a small number: a number would be traded on.
- Market-data health also covers the exchange's own cumulative counter: if `cumulative_volume` jumps by more than the tick reported, `volume_gaps` increments and health turns DEGRADED. Silent tick loss that every other counter misses shows up here.
- `BreakoutRetestEngine` replaces a bar by `start_at` instead of appending, because PA-P3 re-emits corrected bars and two bars at one timestamp would corrupt swing detection. A correction does not advance the state machine; it invalidates an in-flight setup and never revisits a setup that already emitted a SIGNAL.
- Paper trading persists traded `setup_id`s via `traded_setups_path`, so a crash mid-day cannot re-enter a setup after restart. The in-memory ledger alone would be empty after a restart.
- Price Action Intraday PA-P6/P7/P8 reached Gate A: `structure.py` (swings, HH/HL/LH/LL, BOS), `setup.py` (breakout -> retest -> trigger state machine) and `paper.py` (paper trading day) plus the `paper run-day` CLI. Gate B `PA-P7_LIVE_VALIDATED` / `PA-P8_LIVE_VALIDATED` is reached (2026-08-19): the full SIGNAL -> entry -> stop -> exit chain ran on same-day tick-built bars, with `market_data_healthy` taken from the session report rather than a manual flag. PA-P6 swings feed that chain; `docs/development-work.md` does not declare a separate `PA-P6_LIVE_VALIDATED`. See `docs/price-action-p6-p8-design.md`.
- PA-P6 V1 uses the **plateau-aware** swing rule (`structure.DEFAULT_SWING_RULE`). The strict rule is kept for ablation only: over 20 days x 3 symbols it left 2330 with 1 of 20 days classifiable, because Taiwan tick sizes make 5m highs repeat. Plateau lifts overall classifiable days from 35% to 80% while barely moving breakout candidates, so it exposes the same structure more often rather than inventing signals. `directional` is measured but not adopted - it finds 15-22 swings per symbol-day, which is noise.
- A correction to a bar a signalled setup already used is audit-only (`corrections_after_signal`). A correction before any signal invalidates the in-flight setup and then replays the corrected bars to re-derive state, but any SIGNAL that replay would produce is suppressed and counted (`signals_suppressed_by_recompute`). Live and replay cannot be identical under corrections; forcing them to match would mean entering at a bar that closed before the correction arrived. The invariant is "live never trades on information it did not have", not "live == replay".
- A swing needs `swing_n` bars on BOTH sides, so the newest bars are never swings. Do not "confirm" the current bar as a swing: structure would flip on noise and live would stop matching replay.
- A missing or `insufficient_data` RVOL never passes the breakout volume gate. A gate that passes when volume is unknown is not a gate.
- Paper trading checks stop before target inside one bar, never releases a `setup_id` from the ledger, refuses new entries when market data is not HEALTHY, and always closes every position before the day ends. Do not relax any of these: each one exists to stop a specific way of inflating expectancy.
- Paper trade results prove determinism and rule compliance only. A passed Gate B shows that the mechanism runs, not that it makes money. Paper results are not evidence of strategy edge, and the edge measured so far is negative (see the research findings below).
- PA-P9 is complete (2026-08-19). Cost enters R: `PaperTrade` carries `cost_r` / `net_r` / `risk_ticks`, and `summarize_expectancy` reports expectancy, win rate, PF and MDD. Gross and net are always printed side by side. `profit_factor` is `None` when there are no losing trades, never inf or 0.
- Paper fills are stamped at `bar.end_at`, because entries fill at a closed bar's close. Stamping `start_at` printed a price that had not traded yet at that time, which made the report impossible to audit against a chart.
- The 13:25 force exit is a principle, not a parameter. The user cannot hold overnight: a day trade nets out and needs no capital, while an overnight position needs full T+2 settlement, and there is not enough capital for that. Do not propose extending holding past 13:25 or carrying positions overnight as a fix.
- `setup.py` has a pre-entry cost gate: with `DEFAULT_MAX_COST_R = 1.0`, a setup whose round-trip friction is at least its risk is invalidated with reason `cost_exceeds_risk`. The threshold comes from a principle (never pay more friction than the risk you take), not from picking the best-looking row of a table.
- The cost model prices ETFs separately. `is_etf_symbol` tests whether the code starts with `00`; ETFs pay 0.1% tax, which is not halved for day trades, and use a two-level tick (0.01 below 50, 0.05 at 50 and above). `etf_tax_rate` has not been checked against a real settlement statement.
- The research universe is `config/universe.txt`: 37 symbols. They are the 50 symbols the user named, minus 10 ETF/ETN, 2 `DayTrade.No` and 1 `DayTrade.OnlyBuy`. The liquidity ranking comes from Shioaji `snapshots`, because the free FinMind tier cannot fetch the whole market.
- Strategy research findings (2026-08-21 to 08-22, see `docs/development-work.md`):
  - The breakout signal has roughly zero alpha over an unconditional baseline.
  - With the cost gate on, the production rule is net -0.628 R per trade over 744 trades. This was measured on the earlier 12-symbol pool over 639 days, before the universe was expanded.
  - The 37 symbols drift down intraday by about -0.058%, and every half-year from 2024 to 2026 is significantly negative. The bid/ask-bounce artifact explains only about 5% of this.
  - The engine is long-only (`paper.py` hard-codes `side="buy"`), so it pays that drift on every trade.
  - An unconditional short with all passive orders is +0.070 R gross but still -0.066 R net. It is less negative, not profitable; the alpha still needed is about 0.13%.
- Price Action phase numbers collide with this repo's P0-P11: `docs/price-action-intraday-plan.md` P1 is the Shioaji tick adapter, not the old-log importer. Always write `PA-Pn` for Price Action phases.
- The tick to 1m to 5m pipeline is live-validated as a data pipeline. That still does not make paper results strategy evidence.
- `market_data.py` must stay free of Shioaji SDK imports, and any consumer of `ShioajiTickStream` must refuse to produce new entry signals unless `stream.is_healthy` is true.
- Shioaji 1.7.5 upgrade is complete offline (2026-10-02). `pyproject.toml` pins `shioaji==1.7.5`, and `shioaji_compat.py` centralizes the login signature, stock contract lookup (`api.contracts.stocks.get` first, old `api.Contracts.Stocks` only as a fallback) and the enums. The repo-local `.venv` has 1.7.5 (created 2026-10-03; 419 tests pass under it), but the system `python3` still has shioaji 1.3.2. Run anything that imports the Shioaji SDK with `.venv/bin/python`. The real SDK's simulation behavior under 1.7.5 has not been smoke-tested during trading hours.
- Sector Flow V1 已於 2026-10-03 搬到 `~/services/stock/tw-day-trading`（MVP commit 3d6032e），本 repo 不再包含。

## Next Development Priority

Two tracks are open. Do not confuse them.

**Track 1 - Price Action Intraday (active, research stage).** PA-P1 to PA-P9 are code complete, and the data pipeline and paper chain are live-validated. The blocker is no longer plumbing; it is the lack of edge. The direction recorded on 2026-08-22, in order:

1. Make the engine two-sided or short-only. `paper.py` and `setup.py` currently only look for long breakouts. Before any short simulation, check two things that are still unverified: whether short-first day trading needs a margin account, and whether the uptick-rule exemption covers day-tradable symbols.
2. Switch to passive limit orders. This saves about 39% of cost, but zero slippage on passive orders is an optimistic upper bound. Measure the fill rate and the adverse-selection cost before trusting it.
3. Look for the remaining ~0.13% alpha on top of 1 and 2.

Separately, before relying on Shioaji 1.7.5 for live collection, run a gated simulation tick smoke during trading hours with `.venv/bin/python`.

**Track 2 - Trading Day Autonomous Cycle v1 (paused, still valid).**

Do not go back to P8 as if it were unimplemented. P8-P11 already exist. The daily operating chain now has a one-command runner and bundle audit.

Also do not treat `daily-ops` as the whole product. It is an artifact/audit runner. The real operating target is a trading-day state machine:

- 17:30 build tomorrow's candidate list.
- 09:05 or 10:00 start the intraday candidate watch loop.
- 09:05/10:00-13:20 repeatedly evaluate candidate entry and open-position exit strategies.
- 13:20 stop new entries and run force-exit / cancel policy.
- 15:00 build a local GitHub-report dry-run artifact and operator would-send metadata.
- 17:30 build the next-candidate handoff.

Recommended next implementation:

1. Run 3-5 real raw-cache / fixture-backed smoke days with `simulate trading-day-cycle-smoke`.
2. Add a separate explicit gate for Shioaji simulation login/order/cancel side effects; keep it disabled by default.
3. Add a separate explicit gate for real GitHub publish / Telegram operator link send.
4. Keep formal live order blocked until a separate production approval SOP exists.
5. Do not treat smoke OK as strategy edge or live-order readiness.

## Important Docs

Read these before changing behavior:

- `README.md` - current project status and quick start.
- `docs/mvp-roadmap.md` - phase map and acceptance criteria.
- `docs/development-work.md` - detailed implementation history, grill-me reviews, and working commands.
- `docs/data-contracts.md` - data contracts for candidates, replay, simulation, readiness, ops manifest, alerts, and regression cases.
- `docs/trading-day-autonomous-cycle.md` - corrected objective, schedule, architecture gap, and next implementation seed.
- `docs/rebuild-baseline.md` - rebuild baseline from the old project.
- `docs/price-action-intraday-plan.md` - Price Action strategy plan. Its P1-P9 numbering is NOT this repo's P0-P11.
- `docs/price-action-p1-design.md` - PA-P1 tick adapter design and two-gate acceptance.
- `docs/price-action-p2-design.md` - PA-P2 1m aggregation, including why canonical bars are never synthesized.
- `docs/price-action-p3-design.md` - PA-P3 5m aggregation and the 1m correction rule.
- `docs/price-action-p4-design.md` / `docs/price-action-p5-design.md` / `docs/price-action-p6-p8-design.md` - bar store, RVOL, structure / setup / paper.
- `docs/superpowers/specs/2026-10-02-shioaji-1-7-5-upgrade-design.md` - Shioaji 1.7.5 compatibility layer.

When phase status changes, update both `docs/development-work.md` and `docs/mvp-roadmap.md`.

## Architecture Rules

- Prefer pure functions and dataclasses for core logic.
- Keep side effects at CLI / adapter boundaries.
- Use protocols / small ports for external dependencies.
- Do not import Shioaji SDK, TiDB drivers, network clients, or environment loading into core business logic unless the existing boundary already does that.
- Do not introduce a DI container unless the dependency graph actually needs one.
- Keep replay, simulation, readiness, and live design outputs separate.
- If adding daily ops artifacts, include run id and checksums so regression cases can trace them.

## Testing / Verification

Default verification:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall -q src tests
git diff --check
```

Useful smoke commands:

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate callback-smoke \
  --date 2026-05-28 \
  --input examples/shioaji-callback-sequence.sample.json \
  --store reports/2026-05-28-callback-smoke-store.json \
  --output reports/2026-05-28-callback-smoke.json

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate shioaji-smoke \
  --date 2026-05-28 \
  --output reports/2026-05-28-shioaji-smoke.json

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate production-readiness \
  --date 2026-06-02 \
  --store reports/2026-06-02-p6-complete-smoke-store-v2.json \
  --output reports/2026-06-02-p7-production-readiness.json \
  --report-output reports/2026-06-02-p7-production-readiness.md \
  --current-time 12:30

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate ops-run \
  --date 2026-06-04 \
  --candidates-input reports/2026-06-04-candidates.json \
  --allow-outside-session

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate regression-import \
  --manifest reports/2026-06-04-ops/ops_run_manifest.json

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate daily-ops \
  --date 2026-06-04 \
  --skip-ingestion \
  --allow-outside-session \
  --fail-on-audit

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle \
  --date 2026-06-04 \
  --trading-data-input data/raw/finmind/TaiwanStockPrice/2026-06-04/0050.jsonl \
  --candidates-input reports/2026-06-04-candidates.json \
  --intraday-bars-input fixtures/2026-06-04-intraday-bars.json \
  --current-time 09:20 \
  --run-all-stages

PYTHONPATH=src python3 -m tw_day_trading_lab.cli simulate trading-day-cycle-smoke \
  --dates 2026-06-04,2026-06-05,2026-06-06 \
  --cache-dir data/raw \
  --candidates-input-pattern 'reports/{date}-candidates.json' \
  --run-all-stages

make daily-ops DATE=2026-06-04 ARGS="--skip-ingestion --allow-outside-session --fail-on-audit"
```

Do not run gated real Shioaji login/order/cancel smoke unless the task explicitly calls for it and the gate is clear.
Do not run `simulate daily-ops --send-alerts` unless the task explicitly calls for real Telegram alerts.
Do not run `simulate daily-ops --simulation-on` unless the task explicitly calls for Shioaji simulation login/order side effects.

## Commit / Close-out

Before closing a development task:

1. Run the smallest meaningful verification.
2. Update docs if behavior, phase status, or contracts changed.
3. Run grill-me review for each completed phase / sprint.
4. Record residual risks and next step in `docs/development-work.md`.
5. Commit with a concise message.
6. Push `master` to `origin`.

The GitHub repo is private: `https://github.com/ChenHom/tw-day-trading-lab`.

## 台股相關資料夾對照（2026-10-03 盤點）

本機有多個名稱相近、目標不同的台股專案。各資料夾的文件都放同一張表；有變動時請一併更新。

| 資料夾 | GitHub repo | 目標 | CLI | 站台 | 狀態 |
|---|---|---|---|---|---|
| `~/services/stock/tw-day-trading` | `ChenHom/tw-swing-trading-mvp` | 台股**波段**量化交易 MVP：回測、每日模擬（paper / FakeBroker）、風控授權、FIFO 對帳。另有族群資金流報表（2026-10-03 自 lab 搬入）。資料夾名稱是歷史遺留，**不是當沖** | `python3 -m app <account\|market\|simulation\|backtest\|report…>` | 有：`https://192.168.50.109/trading/`（台股波段交易儀表板，`trading-web.service` → 127.0.0.1:8800） | 運作中：平日 15:10 / 15:12 影子模擬、21:00 籌碼同步（cron） |
| `~/services/stock/tw-day-trading-lab` | `ChenHom/tw-day-trading-lab` | 台股**當沖**重建實驗室：候選名單、replay / paper 驗證、Shioaji **模擬**執行鏈驗證 | `tw-daytrade`（`PYTHONPATH=src python3 -m tw_day_trading_lab.cli …`） | 無 | 開發中；無有效排程（crontab 內 8/19–21 的收集排程已過期） |
| `~/services/stock/quantitative-trading-decision-system` | `ChenHom/quantitative-trading-decision-system` | 舊版 Shioaji 盤中當沖機器人；`tw-day-trading-lab` 只把它當資料來源與失敗案例 | `scripts/run_trading_system.sh`、`scripts/run_intraday_event_monitor.sh` | 無 | 程式凍結於 2026-04，但平日 08:30 / 08:58 仍由 cron 以**模擬模式**執行 |
| `~/services/stock/quant-feather-integration` | 無（非 git） | 整合 quantitative-trading-decision-system 與 StrategyExecutor_feather 的骨架 | 無 | 無 | 封存（2026-03） |
| `~/services/stock/StrategyExecutor_feather` | `phenomenoner/StrategyExecutor_feather`（第三方） | 富邦 Neo SDK 當沖機器人，本機分支改寫為 Shioaji | `python strategy_async_demo.py` | 無 | 封存（本機改寫停在 2026-02） |
| `~/services/stock/taiwan-stock-market-evaluation` | 無（非 git） | 空資料夾（只有 `.serena/`） | 無 | 無 | 可刪除 |
| `~/services/AI-Trading-Copilot` | `ChenHom/AI-Trading-Copilot` | FinMind 盤前 / 盤後分析與投資組合助手 | `./run.sh`、`python main.py --mode OPEN\|CLOSE` | 無 | 本機停用；GitHub Actions 排程是否仍啟用未查證 |
| `~/services/stocks-db` | 無遠端（本機 git） | FinMind → 本機 TiDB 匯入 | `./run.sh import-stocks` | 無 | 封存（TiDB 未啟動） |
| `~/services/tw-stock-research-platform` | `ChenHom/tw-stock-research-platform` | 以公開資訊為核心的台股研究決策平台（TypeScript CLI） | `npm run research` 等 | 無 | 停用（`redis-cache` 容器仍在執行） |

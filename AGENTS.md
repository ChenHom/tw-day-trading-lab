# AGENTS.md - tw-day-trading-lab

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
- Price Action Intraday PA-P1 acceptance is split into two gates in `docs/price-action-p1-design.md`. Gate A `PA-P1_CODE_COMPLETE` is reached: `market_data.py` provides `MarketTick`, `normalize_shioaji_tick`, `ShioajiTickStream`, the `data/raw/shioaji/ticks/` raw store, market-data health, and the gated `simulate shioaji-tick-smoke`. Gate B `PA-P1_LIVE_VALIDATED` is NOT reached: it needs one real simulation quote smoke during trading hours whose report shows `live_validation.passed=true`.
- Price Action Intraday PA-P2 reached Gate A `PA-P2_CODE_COMPLETE`: `bars.py` provides `MarketBar`, `OneMinuteBarAggregator`, `aggregate_ticks`, `latest_bars`, `check_bar_volume`, plus `market_data.replay_raw_ticks` and the `bars build` CLI. Gate B `PA-P2_LIVE_VALIDATED` is NOT reached: it needs a real tick session aggregated and compared against post-market provider 1m data. See `docs/price-action-p2-design.md`.
- Canonical 1m has NO synthetic bars: a minute with no trades produces no `MarketBar`. This deliberately overrides the Missing Minute Policy in `docs/price-action-intraday-plan.md`, so "real zero volume", "nobody traded" and "the feed dropped data" stay distinguishable. Do not add `is_synthetic` back at the canonical layer.
- Price Action Intraday PA-P3 reached Gate A `PA-P3_CODE_COMPLETE`: `bars.FiveMinuteBarAggregator` / `aggregate_1m_to_5m` build canonical 5m from canonical 1m. Gate B `PA-P3_LIVE_VALIDATED` is NOT reached. See `docs/price-action-p3-design.md`.
- 5m is never taken from a provider and never from another 5m: `FiveMinuteBarAggregator.on_bar` accepts only `timeframe == "1m"`. A 5m bucket is a time range, not five bars, and a corrected 1m replaces its minute and forces a full bucket recompute - it is never added to the previous revision.
- `bars.py` must stay provider-agnostic: its only project import is `MarketTick`. Both aggregators advance on event time only, never the wall clock, so replay and live produce identical bars.
- Price Action Intraday PA-P4 is complete: `bars.append_bars` / `read_bar_events` / `load_latest_bars` / `load_bar_revision` persist bars to an append-only JSONL event store at `data/bars/{timeframe}/{date}/{symbol}.jsonl`. Timeframe is a directory level so 1m and 5m can never collide. A correction is a new line, never an overwrite. PA-P4 has no Gate B. See `docs/price-action-p4-design.md`.
- PA-P5 (TOD-RVOL) is BLOCKED on a data source. FinMind `TaiwanStockKBar` (minute K) returns HTTP 400 `Your level is free. Please update your user level.` - it needs a paid sponsor token. Verified 2026-08-16 against the live API. Daily `TaiwanStockPrice` is unaffected and works anonymously. Do not start PA-P5 before the baseline source is decided: paid FinMind token, Shioaji `api.kbars(contract, start, end)` pre-market backfill, or self-accumulating 20 sessions of our own tick store.
- Price Action phase numbers collide with this repo's P0-P11: `docs/price-action-intraday-plan.md` P1 is the Shioaji tick adapter, not the old-log importer. Always write `PA-Pn` for Price Action phases.
- Until every PA Gate B passes, do not claim the tick to 1m to 5m pipeline is usable for daily paper trading.
- `market_data.py` must stay free of Shioaji SDK imports, and any consumer of `ShioajiTickStream` must refuse to produce new entry signals unless `stream.is_healthy` is true.

## Next Development Priority

Two tracks are open. Do not confuse them.

**Track 1 - Price Action Intraday (active).** PA-P1 / PA-P2 / PA-P3 all reached Gate A. The next action is not more code: it is one real simulation quote smoke during trading hours, which closes three Gate Bs at once.

1. `simulate shioaji-tick-smoke --symbols 2330 --duration-seconds 60 --enable-tick-stream`, then check `live_validation.passed`.
2. Verify the FinMind `TaiwanStockPriceMinute` volume unit before any 1m comparison; it is still unverified and there is no minute cache on disk at all.
3. Compare `bars build` output against post-market provider 1m, then the 5m.
4. Only then start PA-P4 (persistence / bar revision store) or PA-P5 (TOD-RVOL).

PA-P5 needs 20-30 trading days of historical 1m per candidate. Schedule that backfill early.

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
- `docs/price-action-p1-design.md` - PA-P1 tick adapter design, two-gate acceptance, and the pending live smoke.
- `docs/price-action-p2-design.md` - PA-P2 1m aggregation, including why canonical bars are never synthesized.
- `docs/price-action-p3-design.md` - PA-P3 5m aggregation and the 1m correction rule.

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

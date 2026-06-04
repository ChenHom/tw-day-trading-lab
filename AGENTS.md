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
- Trading Day Autonomous Cycle v1 steps 1-2 are complete: `simulate trading-day-cycle` emits `trading_day_run_state.json`, and trading-day status is based only on API/raw-cache data availability. Do not add a separate holiday calendar unless explicitly requested.
- `.pfx` / `.p12` files are ignored, and `Sinopac.pfx` has been removed from reachable Git history.

## Next Development Priority

The next coding phase is `Trading Day Autonomous Cycle v1`.

Do not go back to P8 as if it were unimplemented. P8-P11 already exist. The daily operating chain now has a one-command runner and bundle audit.

Also do not treat `daily-ops` as the whole product. It is an artifact/audit runner. The real operating target is a trading-day state machine:

- 17:30 build tomorrow's candidate list.
- 09:05 or 10:00 start the intraday candidate watch loop.
- 09:05/10:00-13:20 repeatedly evaluate candidate entry and open-position exit strategies.
- 13:20 stop new entries and run force-exit / cancel policy.
- 15:00 publish the trading-day report to GitHub and send the operator link.
- 17:30 build the next candidate list and hand off to the next trading day.

Recommended next implementation:

1. Add fixture-based intraday watch loop tests for repeated candidate checks, entry trigger, exit trigger, no-action candidate, and 13:20 stop.
2. Implement the intraday candidate watch loop against the existing candidate list only; do not scan the whole market intraday.
3. Connect entry / exit strategy evaluation to position state so open positions are checked for exits before new entries.
4. Add execution policy tests for duplicate intents, pending orders, partial fills, callback ordering, and force-exit behavior.
5. Add 15:00 GitHub report publish dry-run and 17:30 next-candidate handoff artifacts.
6. Only after the dry-run trading-day cycle is auditable should 3-5 day stability observation become the acceptance evidence.
7. Keep real alert sending, Shioaji simulation side effects, and live order execution blocked unless a separate task explicitly designs and verifies the gate.

## Important Docs

Read these before changing behavior:

- `README.md` - current project status and quick start.
- `docs/mvp-roadmap.md` - phase map and acceptance criteria.
- `docs/development-work.md` - detailed implementation history, grill-me reviews, and working commands.
- `docs/data-contracts.md` - data contracts for candidates, replay, simulation, readiness, ops manifest, alerts, and regression cases.
- `docs/trading-day-autonomous-cycle.md` - corrected objective, schedule, architecture gap, and next implementation seed.
- `docs/rebuild-baseline.md` - rebuild baseline from the old project.

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

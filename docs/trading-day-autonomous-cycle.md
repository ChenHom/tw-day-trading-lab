# Trading Day Autonomous Cycle

Date: 2026-06-04

## Objective Alignment

The project objective is not only to produce a daily simulation bundle. The objective is a repeatable trading-day operating cycle:

1. Use the prepared candidate stock list as the watch universe.
2. During the trading window, repeatedly evaluate candidate stocks against explicit entry and exit strategies.
3. Execute approved day-trading actions through a gated execution adapter.
4. Stop new trading and force normal close-out by 13:20.
5. After the market close buffer, publish the trading-day review and send the GitHub link to the operator.
6. Build the candidate list for the next trading day at 17:30.
7. Repeat automatically on every valid trading day.

This document corrects the project direction after the `Daily Simulation Ops Automation v1` work. That work remains useful, but it is a bundle runner and audit layer, not the full autonomous trading-day loop.

## Target Schedule

| Time | Stage | Required behavior |
|---|---|---|
| Previous day / 17:30 | Candidate build | Build and persist the candidate list for the next trading day. |
| 09:05 or 10:00 | Intraday start | Load the candidate list, confirm trading day/session gates, and start the watch loop. |
| 09:05/10:00-13:20 | Watch and execute | Poll candidate symbols, build entry/exit signals, apply risk gates, place approved orders, track callbacks and positions. |
| 13:20 | Stop and force exit | Stop new entries, cancel stale orders, force close intraday positions according to policy, and freeze the trading run. |
| 13:30-14:00 | Close buffer | Wait for market close data to settle before final accounting. |
| 15:00 | Review and publish | Produce the trading result report, pros/cons, operational findings, GitHub artifact, and operator link. |
| 17:30 | Next candidates | Build and review tomorrow's candidate list, then store it as the next trading-day input. |

The 09:05 versus 10:00 start is a policy choice. The system should support both and record which policy was used in the run state.

## Current Architecture Verdict

The architecture is partially on track:

- Good foundation: candidate engine, replay, cost model, risk and exit checks, Shioaji simulation boundary, callback sync, readiness report, alerts, regression import, and daily bundle audit already exist.
- Direction gap: the current `simulate daily-ops` command is a single orchestration pass. It does not run a time-aware intraday watch loop from 09:05/10:00 to 13:20.
- Execution gap: the repo can generate one simulation plan from candidates, but it does not yet continuously re-check live/intraday bars, open positions, exit triggers, partial fills, and stale orders during the trading window.
- Ops gap: the repo can create local reports, but it does not yet implement the 15:00 GitHub publish-and-link stage or the 17:30 next-day candidate scheduler.
- Automation gap: there is no trading-day scheduler/state machine that knows holidays, run stages, retries, locks, and operator notifications across the full day.

Therefore the corrected next phase is not "3-5 day stability observation" as the primary goal. The corrected next phase is `Trading Day Autonomous Cycle v1`, built behind simulation/fake/live gates.

## Corrected Phase Plan

### C1: Trading Day Scheduler and Run State

Status: dry-run state machine complete.

Implement a scheduler/state machine for a single trading date.

Required contracts:

- `trading_day_run_id`
- trading date and calendar status
- stage: `candidate_ready`, `intraday_waiting`, `intraday_running`, `force_exit`, `close_buffer`, `reporting`, `next_candidates`, `complete`, `blocked`
- configured start time: `09:05` or `10:00`
- hard stop time: `13:20`
- report time: `15:00`
- next-candidate time: `17:30`
- lock file and retry policy
- artifact manifest with checksums
- trading-day status based on API/raw-cache data availability only; no separate holiday calendar

Acceptance:

- A dry-run command can simulate each stage transition for a fixture trading day. Completed with `simulate trading-day-cycle --run-all-stages`.
- Missing API/raw-cache trading rows produce `calendar_status=non_trading_day` and `stage=blocked`.
- Re-running the same trading day is idempotent and does not duplicate orders or reports. Dry-run state now records idempotency key / lock policy; side-effect enforcement remains for C3/C4.

### C2: Intraday Candidate Watch Loop

Implement the loop that repeatedly evaluates only the prepared candidate list.

Required behavior:

- Load candidate list from a dated artifact.
- Fetch or receive intraday bars/ticks through a market-data port.
- Evaluate entry strategy per candidate.
- Evaluate exit strategy for open positions every loop.
- Record every skipped candidate with a reason.
- Record signal snapshots for replay/regression.

Acceptance:

- Fixture intraday bars can produce one approved entry, one rejected entry, one exit, and one no-action candidate.
- The watch loop stops at 13:20 even when candidate signals still appear.

### C3: Execution and Force-Exit Policy

Connect approved entry/exit signals to the gated execution adapter.

Required behavior:

- Keep formal live trading blocked by default.
- Support fake/dry-run and Shioaji simulation behind explicit flags.
- Enforce max positions, max daily loss, max pending orders, duplicate intent protection, partial-fill handling, and callback ordering checks.
- At 13:20, stop new entries and trigger force-exit/cancel policy.

Acceptance:

- Fixture run proves no new entry after 13:20.
- Pending orders and open positions produce explicit manual actions or simulated close-out results.

### C4: 15:00 GitHub Review and Operator Link

Publish the trading-day result after the close buffer.

Required behavior:

- Generate a Markdown report with trades, candidates watched, entries, exits, skipped signals, P/L or simulation P/L, pros, cons, blockers, and next actions.
- Commit or otherwise publish the dated report to GitHub.
- Send the operator a link to the GitHub artifact.
- Keep Telegram sending gated and deduped.

Acceptance:

- Dry-run mode creates the report and prints the would-send link.
- Real send requires explicit notification gate.

### C5: 17:30 Next-Day Candidate Builder

Build tomorrow's candidate list after the trading-day review.

Required behavior:

- Ingest or validate post-market data.
- Build next-day candidates.
- Persist the candidate run.
- Publish candidate summary and data gaps.
- Attach candidate artifact to the next trading-day run state.

Acceptance:

- A fixture post-market run produces a next-day candidate file and links it to the next trading date.

## Safety Boundary

This correction does not mean the next implementation should enable real live orders.

Allowed next work:

- dry-run trading-day scheduler
- fixture intraday watch loop
- Shioaji simulation-only execution behind explicit `--simulation-on`
- report publish dry-run
- GitHub artifact publishing once secrets and destination are explicitly safe

Still blocked:

- `sj.Shioaji(simulation=False)`
- real live orders
- real cancel of live orders
- treating simulation P/L as strategy edge
- sending Telegram/GitHub links without an explicit send gate in automation

## Next Implementation Seed

Build `Trading Day Autonomous Cycle v1` in small steps:

1. Add a dry-run `simulate trading-day-cycle` command with stage simulation and a `trading_day_run_state.json` artifact. Done.
2. Add fixture-based intraday watch loop tests for 09:05/10:00 start, repeated candidate checks, and 13:20 stop. Done.
3. Add fixture-based entry / exit strategy loop wiring: candidate entry via `VwapBreakoutStrategy`, open-position exit first, and `watch_events.json`. Done.
4. Add execution policy tests for duplicate intents, pending orders, partial fills, and force-exit at 13:20. Done as dry-run artifact policy.
5. Add report publish dry-run for the 15:00 GitHub report/link step. Done as local report / would-send metadata.
6. Add next-candidate builder handoff for the 17:30 stage. Done as `next_api_available_trading_day` handoff.
7. Run 3-5 fixture / simulation-side-effect gated smoke days before any stronger automation gate.

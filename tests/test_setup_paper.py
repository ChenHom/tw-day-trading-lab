import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tw_day_trading_lab.bars import STATUS_CLOSED, MarketBar
from tw_day_trading_lab.cli import _resolve_market_data_health
from tw_day_trading_lab.paper import (
    EXIT_FORCE,
    EXIT_PRE_CLOSE,
    EXIT_STOP,
    EXIT_TARGET,
    load_traded_setups,
    run_paper_trading_day,
    summarize_expectancy,
)
from tw_day_trading_lab.rvol import RvolResult
from tw_day_trading_lab.setup import (
    INVALIDATED,
    SIGNAL,
    STOP_ENTRY_BAR_LOW,
    STOP_FIXED_TICKS,
    WAIT_RETEST,
    WAIT_TRIGGER,
    BreakoutRetestEngine,
)

DATE = "2026-08-17"


def bar(index, high, low, close, symbol="2330", start_hour=9, start_minute=0):
    total = start_minute + index * 5
    hour, minute = start_hour + total // 60, total % 60
    # A 5m bar ends five minutes later, not five seconds. The old fixture made
    # end_at indistinguishable from start_at for any minute-level comparison.
    end = total + 5
    end_hour, end_minute = start_hour + end // 60, end % 60
    return MarketBar(
        symbol=symbol,
        timeframe="5m",
        start_at=f"{DATE}T{hour:02d}:{minute:02d}:00",
        end_at=f"{DATE}T{end_hour:02d}:{end_minute:02d}:00",
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1000,
        trade_count=1,
        status=STATUS_CLOSED,
        revision=1,
        source="local_1m_aggregated",
    )


def rvol(value, symbol="2330"):
    return RvolResult(
        symbol=symbol,
        timeframe="5m",
        start_at="",
        slot="",
        volume=0,
        cumulative_volume=0,
        tod_rvol=value,
        cum_rvol=value,
        tod_baseline=1000.0,
        cum_baseline=1000.0,
        sample_days=20,
        status="ok",
    )


# Bars 0-4 build a confirmed swing high of 110 at index 2.
BASE = [
    (105, 100, 102),
    (107, 101, 104),
    (110, 103, 106),
    (106, 100, 102),
    (105, 99, 101),
]


def build(extra, symbol="2330"):
    return [bar(index, *point, symbol=symbol) for index, point in enumerate([*BASE, *extra])]


def feed(bars, rvols, engine=None):
    """rvols: index -> RvolResult (missing index means no rvol for that bar)."""
    engine = engine or BreakoutRetestEngine()
    events = []
    for index, item in enumerate(bars):
        event = engine.on_bar(item, rvols.get(index))
        if event is not None:
            events.append(event)
    return engine, events


class BreakoutTest(unittest.TestCase):
    def test_close_above_a_confirmed_swing_high_with_volume_breaks_out(self):
        bars = build([(115, 108, 114)])

        _engine, events = feed(bars, {5: rvol(2.0)})

        self.assertEqual([event.state for event in events], [WAIT_RETEST])
        self.assertEqual(events[0].breakout_level, 110)
        self.assertEqual(events[0].reason, "breakout_confirmed")

    def test_low_volume_does_not_break_out(self):
        bars = build([(115, 108, 114)])

        _engine, events = feed(bars, {5: rvol(1.2)})

        self.assertEqual(events, [])

    def test_missing_rvol_does_not_break_out(self):
        """A volume gate that passes when volume is unknown is not a gate."""
        bars = build([(115, 108, 114)])

        _engine, events = feed(bars, {})

        self.assertEqual(events, [])

    def test_insufficient_rvol_history_does_not_break_out(self):
        bars = build([(115, 108, 114)])
        short = RvolResult(
            symbol="2330", timeframe="5m", start_at="", slot="", volume=0,
            cumulative_volume=0, tod_rvol=None, cum_rvol=None, tod_baseline=1000.0,
            cum_baseline=1000.0, sample_days=3, status="insufficient_data",
        )

        _engine, events = feed(bars, {5: short})

        self.assertEqual(events, [])

    def test_no_confirmed_swing_means_no_breakout(self):
        bars = [bar(0, 105, 100, 104), bar(1, 120, 101, 119)]

        _engine, events = feed(bars, {1: rvol(3.0)})

        self.assertEqual(events, [])

    def test_a_breakout_level_is_only_used_once(self):
        bars = build([(115, 108, 114), (116, 111, 115), (117, 112, 116)])

        _engine, events = feed(bars, {5: rvol(2.0), 6: rvol(2.0), 7: rvol(2.0)})

        self.assertEqual(sum(1 for event in events if event.reason == "breakout_confirmed"), 1)


class RetestTriggerTest(unittest.TestCase):
    def test_full_breakout_retest_trigger_produces_one_signal(self):
        bars = build(
            [
                (115, 108, 114),   # breakout above 110
                (114, 110, 111),   # retest back to the level
                (118, 111, 117),   # close above the post-retest local high
            ]
        )

        _engine, events = feed(bars, {5: rvol(2.0)})

        self.assertEqual([event.state for event in events], [WAIT_RETEST, WAIT_TRIGGER, SIGNAL])
        signal = events[-1]
        self.assertEqual(signal.entry_price, 117)
        self.assertEqual(signal.stop_price, 110)
        self.assertEqual(signal.target_price, 117 + 2 * (117 - 110))
        self.assertTrue(signal.setup_id)

    def test_losing_the_breakout_level_invalidates_before_any_retest(self):
        bars = build([(115, 108, 114), (112, 104, 105)])

        _engine, events = feed(bars, {5: rvol(2.0)})

        self.assertEqual(events[-1].state, INVALIDATED)
        self.assertEqual(events[-1].reason, "breakout_level_lost")

    def test_retest_window_expiry_invalidates(self):
        far = [(130, 125, 129)] * 6
        bars = build([(115, 108, 114), *far])

        _engine, events = feed(bars, {5: rvol(2.0)})

        self.assertEqual(events[-1].state, INVALIDATED)
        self.assertEqual(events[-1].reason, "retest_window_expired")

    def test_losing_the_retest_low_invalidates_before_trigger(self):
        bars = build([(115, 108, 114), (114, 110, 111), (112, 105, 106)])

        _engine, events = feed(bars, {5: rvol(2.0)})

        self.assertEqual([event.state for event in events][-1], INVALIDATED)
        self.assertEqual(events[-1].reason, "retest_low_lost")

    def test_no_trigger_means_no_signal(self):
        bars = build([(115, 108, 114), (114, 110, 111), (113, 110, 112)])

        _engine, events = feed(bars, {5: rvol(2.0)})

        self.assertEqual([event.state for event in events], [WAIT_RETEST, WAIT_TRIGGER])

    def test_setup_ids_are_stable_across_replays(self):
        bars = build([(115, 108, 114), (114, 110, 111), (118, 111, 117)])

        runs = [
            [event.to_dict() for event in feed(bars, {5: rvol(2.0)})[1]] for _ in range(3)
        ]

        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])


class CorrectedBarTest(unittest.TestCase):
    """PA-P3 can re-emit a bar at a higher revision; PA-P7 must not duplicate it."""

    def _corrected(self, source, **overrides):
        return MarketBar(
            **{**source.to_dict(), "revision": 2, "status": "CORRECTED", **overrides}
        )

    def test_a_corrected_bar_replaces_instead_of_duplicating(self):
        bars = build([])
        engine = BreakoutRetestEngine()
        for item in bars:
            engine.on_bar(item)

        engine.on_bar(self._corrected(bars[2], high=140))

        self.assertEqual(engine.corrections_applied, 1)
        stored = engine._symbols["2330"].bars
        self.assertEqual(len(stored), len(bars))
        self.assertEqual([b.start_at for b in stored], sorted(b.start_at for b in bars))
        self.assertEqual(stored[2].high, 140)

    def test_a_correction_does_not_advance_the_state_machine(self):
        bars = build([])
        engine = BreakoutRetestEngine()
        for item in bars:
            engine.on_bar(item)

        event = engine.on_bar(self._corrected(bars[2], high=140))

        self.assertIsNone(event)
        self.assertEqual(engine.state_of("2330"), "WAIT_BREAKOUT")

    def test_a_correction_during_an_open_setup_invalidates_it(self):
        bars = build([(115, 108, 114)])
        engine, events = feed(bars, {5: rvol(2.0)})
        self.assertEqual(engine.state_of("2330"), WAIT_RETEST)

        event = engine.on_bar(self._corrected(bars[2], high=140))

        self.assertEqual(event.state, INVALIDATED)
        self.assertEqual(event.reason, "corrected_bar_in_setup")
        self.assertEqual(engine.state_of("2330"), "WAIT_BREAKOUT")

    def test_recompute_restores_state_from_corrected_history(self):
        """A correction re-derives the setup instead of losing it for the day."""
        bars = build([(115, 108, 114), (114, 110, 111)])
        engine, events = feed(bars, {5: rvol(2.0)})
        self.assertEqual(engine.state_of("2330"), WAIT_TRIGGER)

        # Correct an early bar that does not change the swing high.
        engine.on_bar(self._corrected(bars[0], close=103.0))

        # The setup is dropped, then rebuilt from corrected bars: back in flight.
        self.assertEqual(engine.corrections_applied, 1)
        self.assertEqual(engine.state_of("2330"), WAIT_TRIGGER)

    def test_a_correction_after_a_signal_is_audit_only(self):
        bars = build([(115, 108, 114), (114, 110, 111), (118, 111, 117)])
        engine, events = feed(bars, {5: rvol(2.0)})
        self.assertEqual(events[-1].state, SIGNAL)

        event = engine.on_bar(self._corrected(bars[2], high=140))

        self.assertIsNone(event)
        self.assertEqual(engine.corrections_after_signal, 1)
        self.assertEqual(engine.signals_suppressed_by_recompute, 0)

    def test_a_recomputed_signal_is_suppressed_not_traded(self):
        """A signal derived from data that arrived late would be look-ahead."""
        bars = build([(115, 108, 114), (114, 110, 111), (113, 110, 112)])
        engine, events = feed(bars, {5: rvol(2.0)})
        self.assertEqual([e.state for e in events], [WAIT_RETEST, WAIT_TRIGGER])

        # The last bar is corrected upward so the replay would now trigger.
        engine.on_bar(self._corrected(bars[7], high=118, close=117))

        self.assertEqual(engine.signals_suppressed_by_recompute, 1)
        self.assertEqual(engine.corrections_after_signal, 0)


    def test_a_stale_revision_is_ignored(self):
        bars = build([])
        engine = BreakoutRetestEngine()
        for item in bars:
            engine.on_bar(item)
        engine.on_bar(self._corrected(bars[2], high=140))

        engine.on_bar(bars[2])

        self.assertEqual(engine.stale_bars, 1)
        self.assertEqual(engine._symbols["2330"].bars[2].high, 140)

    def test_a_repeated_identical_bar_is_not_a_correction(self):
        bars = build([])
        engine = BreakoutRetestEngine()
        for item in bars:
            engine.on_bar(item)

        engine.on_bar(bars[2])

        self.assertEqual(engine.corrections_applied, 0)
        self.assertEqual(len(engine._symbols["2330"].bars), len(bars))


def winning_day(symbol="2330"):
    """Breakout, retest, trigger, then the 2R target is reached."""
    return build([(115, 108, 114), (114, 110, 111), (118, 111, 117), (132, 116, 131)], symbol)


class PaperTradingTest(unittest.TestCase):
    def _rvol_map(self, symbols=("2330",)):
        return {f"{symbol}|{DATE}T09:25:00": rvol(2.0, symbol) for symbol in symbols}

    def test_target_exit_records_a_full_trade_lifecycle(self):
        report = run_paper_trading_day(
            trading_date=DATE, bars=winning_day(), rvol_by_key=self._rvol_map()
        )

        self.assertEqual(report["summary"]["trades"], 1)
        trade = report["trades"][0]
        self.assertEqual(trade["entry_price"], 117)
        self.assertEqual(trade["stop_price"], 110)
        self.assertEqual(trade["target_price"], 131)
        self.assertEqual(trade["exit_reason"], EXIT_TARGET)
        self.assertAlmostEqual(trade["r"], 2.0)
        for key in ("setup_id", "symbol", "entry_time", "exit_time", "exit_price"):
            self.assertTrue(trade[key], key)

    def test_stop_exit_gives_minus_one_r(self):
        bars = build([(115, 108, 114), (114, 110, 111), (118, 111, 117), (118, 109, 110)])

        report = run_paper_trading_day(
            trading_date=DATE, bars=bars, rvol_by_key=self._rvol_map()
        )

        trade = report["trades"][0]
        self.assertEqual(trade["exit_reason"], EXIT_STOP)
        self.assertAlmostEqual(trade["r"], -1.0)

    def test_fill_times_are_the_bar_close_not_the_bar_open(self):
        # A fill is priced off a closed bar, so it happens at that bar's end.
        # Labelling it with the bar's start makes the report claim a fill at a
        # price that had not traded yet, which is unauditable against a chart.
        bars = build([(115, 108, 114), (114, 110, 111), (118, 111, 117), (118, 109, 110)])
        signal_bar = bars[-2]
        exit_bar = bars[-1]

        report = run_paper_trading_day(
            trading_date=DATE, bars=bars, rvol_by_key=self._rvol_map()
        )

        trade = report["trades"][0]
        self.assertEqual(trade["entry_price"], signal_bar.close)
        self.assertEqual(trade["entry_time"], signal_bar.end_at)
        self.assertEqual(trade["exit_time"], exit_bar.end_at)
        self.assertNotEqual(trade["entry_time"], signal_bar.start_at)

    def test_a_bar_covering_both_levels_is_assumed_to_hit_the_stop(self):
        bars = build([(115, 108, 114), (114, 110, 111), (118, 111, 117), (140, 105, 138)])

        report = run_paper_trading_day(
            trading_date=DATE, bars=bars, rvol_by_key=self._rvol_map()
        )

        self.assertEqual(report["trades"][0]["exit_reason"], EXIT_STOP)

    def test_unhealthy_market_data_blocks_new_entries(self):
        report = run_paper_trading_day(
            trading_date=DATE,
            bars=winning_day(),
            rvol_by_key=self._rvol_map(),
            market_data_healthy=False,
        )

        self.assertEqual(report["summary"]["trades"], 0)
        self.assertEqual(report["skipped"][0]["reason"], "market_data_unhealthy")

    def test_daily_entry_cap_is_enforced(self):
        bars = []
        rvols = {}
        for index, symbol in enumerate(("2330", "2317", "6180")):
            bars.extend(winning_day(symbol))
            rvols[f"{symbol}|{DATE}T09:25:00"] = rvol(2.0, symbol)

        report = run_paper_trading_day(
            trading_date=DATE, bars=bars, rvol_by_key=rvols, max_new_entries=2
        )

        self.assertEqual(report["summary"]["entries"], 2)
        self.assertEqual(
            [item["reason"] for item in report["skipped"]], ["max_new_entries"]
        )

    def test_no_entry_after_the_hard_stop(self):
        late = [
            bar(index, *point, start_hour=13, start_minute=0)
            for index, point in enumerate([*BASE, (115, 108, 114), (114, 110, 111), (118, 111, 117)])
        ]

        report = run_paper_trading_day(
            trading_date=DATE,
            bars=late,
            rvol_by_key={f"2330|{DATE}T13:25:00": rvol(2.0)},
        )

        self.assertEqual(report["summary"]["trades"], 0)
        self.assertEqual(report["skipped"][0]["reason"], "after_hard_stop")

    def test_open_position_is_force_exited_before_the_close(self):
        bars = winning_day()[:-1]
        bars.append(bar(0, 120, 116, 119, start_hour=13, start_minute=25))

        report = run_paper_trading_day(
            trading_date=DATE, bars=bars, rvol_by_key=self._rvol_map()
        )

        trade = report["trades"][0]
        self.assertEqual(trade["exit_reason"], EXIT_FORCE)
        self.assertEqual(report["summary"]["open_positions"], 0)

    def test_force_exit_fills_at_1325_not_in_the_closing_auction(self):
        # 13:25 to 13:30 is the closing auction: no continuous trading, so an
        # exit that lands there is not an exit at a price anyone chose.
        bars = winning_day()[:-1]
        bars.append(bar(0, 120, 116, 119, start_hour=13, start_minute=20))
        bars.append(bar(0, 121, 117, 120, start_hour=13, start_minute=25))

        report = run_paper_trading_day(
            trading_date=DATE, bars=bars, rvol_by_key=self._rvol_map()
        )

        trade = report["trades"][0]
        self.assertEqual(trade["exit_reason"], EXIT_FORCE)
        self.assertEqual(trade["exit_time"], f"{DATE}T13:25:00")
        self.assertEqual(trade["exit_price"], 119)

    def test_nothing_is_left_open_even_without_a_force_exit_bar(self):
        report = run_paper_trading_day(
            trading_date=DATE, bars=winning_day()[:-1], rvol_by_key=self._rvol_map()
        )

        self.assertEqual(report["summary"]["open_positions"], 0)
        self.assertEqual(report["trades"][0]["exit_reason"], EXIT_PRE_CLOSE)

    def test_one_symbol_cannot_hold_two_positions_at_once(self):
        bars = winning_day()
        # A second identical setup on the same symbol later in the day.
        bars.extend(
            bar(index, *point, start_hour=11)
            for index, point in enumerate([*BASE, (115, 108, 114), (114, 110, 111), (118, 111, 117)])
        )
        rvols = self._rvol_map()
        rvols[f"2330|{DATE}T11:25:00"] = rvol(2.0)

        report = run_paper_trading_day(trading_date=DATE, bars=bars, rvol_by_key=rvols)

        self.assertLessEqual(report["summary"]["entries"], 2)
        self.assertEqual(report["summary"]["open_positions"], 0)

    def test_replaying_the_same_day_gives_identical_trades(self):
        runs = [
            run_paper_trading_day(
                trading_date=DATE, bars=winning_day(), rvol_by_key=self._rvol_map()
            )["trades"]
            for _ in range(3)
        ]

        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_a_restart_cannot_re_enter_a_setup_already_traded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "traded.jsonl"

            first = run_paper_trading_day(
                trading_date=DATE,
                bars=winning_day(),
                rvol_by_key=self._rvol_map(),
                traded_setups_path=path,
            )
            # Same day replayed after a crash: the ids are on disk.
            second = run_paper_trading_day(
                trading_date=DATE,
                bars=winning_day(),
                rvol_by_key=self._rvol_map(),
                traded_setups_path=path,
            )

        self.assertEqual(first["summary"]["trades"], 1)
        self.assertEqual(second["summary"]["trades"], 0)
        self.assertEqual(second["skipped"][0]["reason"], "already_traded")

    def test_traded_setup_ids_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "traded.jsonl"

            report = run_paper_trading_day(
                trading_date=DATE,
                bars=winning_day(),
                rvol_by_key=self._rvol_map(),
                traded_setups_path=path,
            )
            stored = load_traded_setups(path)

        self.assertEqual(stored, {report["trades"][0]["setup_id"]})

    def test_without_a_path_nothing_is_written(self):
        report = run_paper_trading_day(
            trading_date=DATE, bars=winning_day(), rvol_by_key=self._rvol_map()
        )

        self.assertEqual(report["summary"]["trades"], 1)
        self.assertEqual(load_traded_setups(None), set())

    def test_a_day_with_no_setup_produces_no_trade_and_no_crash(self):
        report = run_paper_trading_day(trading_date=DATE, bars=build([]), rvol_by_key={})

        self.assertEqual(report["summary"]["trades"], 0)
        self.assertEqual(report["summary"]["entries"], 0)
        self.assertEqual(report["trades"], [])


class MarketDataHealthResolutionTest(unittest.TestCase):
    """PA-P8 must take health from the PA-P1 report, not from a human flag."""

    def _args(self, **overrides):
        args = Namespace(session_report=None, assume_healthy=False)
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def _report(self, tmp, health, passed):
        path = Path(tmp) / "session.json"
        path.write_text(
            json.dumps(
                {
                    "health": health,
                    "live_validation": {"passed": passed},
                    "summary": {"volume_gaps": 0 if passed else 3},
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_healthy_report_allows_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _resolve_market_data_health(
                self._args(session_report=str(self._report(tmp, "HEALTHY", True)))
            )

        self.assertTrue(result["healthy"])
        self.assertEqual(result["source"], "session_report")

    def test_degraded_report_blocks_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _resolve_market_data_health(
                self._args(session_report=str(self._report(tmp, "DEGRADED", False)))
            )

        self.assertFalse(result["healthy"])
        self.assertEqual(result["volume_gaps"], 3)

    def test_no_report_fails_closed(self):
        """Nobody supplying evidence is not the same as evidence of health."""
        result = _resolve_market_data_health(self._args())

        self.assertFalse(result["healthy"])
        self.assertEqual(result["source"], "no_session_report")

    def test_missing_report_file_fails_closed(self):
        result = _resolve_market_data_health(self._args(session_report="/nope/session.json"))

        self.assertFalse(result["healthy"])
        self.assertEqual(result["source"], "session_report_missing")

    def test_assume_healthy_is_an_explicit_opt_out(self):
        result = _resolve_market_data_health(self._args(assume_healthy=True))

        self.assertTrue(result["healthy"])
        self.assertEqual(result["source"], "assumed_healthy")


class CostGateTest(unittest.TestCase):
    """A signal whose friction exceeds its risk is not a signal.

    Reuses the ablation entry-bar-low stop so the stop distance is set by the
    bar's low, which makes a one tick stop easy to construct.
    """

    def _signal(self, low, **options):
        engine = BreakoutRetestEngine(
            require_retest=False, require_trigger=False,
            stop_rule=STOP_ENTRY_BAR_LOW, **options
        )
        _, events = feed(build([(115, low, 114)]), {5: rvol(2.0)}, engine)
        return events[-1]

    def test_a_stop_costing_more_than_it_risks_is_not_signalled(self):
        # 114 with a one tick stop: cost_r is 1.54, so the 2R target is gone
        # before the market moves. No hit rate repairs that.
        event = self._signal(113.5)

        self.assertEqual(event.state, INVALIDATED)
        self.assertEqual(event.reason, "cost_exceeds_risk")

    def test_a_wide_enough_stop_still_signals(self):
        event = self._signal(108)

        self.assertEqual(event.state, SIGNAL)
        self.assertEqual(event.stop_price, 108)

    def test_the_gate_can_be_turned_off_for_ablation(self):
        event = self._signal(113.5, max_cost_r=None)

        self.assertEqual(event.state, SIGNAL)

    def test_a_non_positive_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            BreakoutRetestEngine(max_cost_r=0)


class AblationFlagsTest(unittest.TestCase):
    """Stages can be switched off for the Research Rule ablation, and only that.

    BASE builds a confirmed swing high of 110 at index 2; index 5 closes 114
    above it, index 6 retests it.
    """

    BREAKOUT = (115, 108, 114)
    RETEST = (114, 110, 111)

    def test_without_retest_the_breakout_bar_signals(self):
        engine = BreakoutRetestEngine(
            require_retest=False, require_trigger=False, stop_rule=STOP_ENTRY_BAR_LOW
        )

        _, events = feed(build([self.BREAKOUT]), {5: rvol(2.0)}, engine)

        self.assertEqual([e.state for e in events], [SIGNAL])
        signal = events[0]
        self.assertEqual(signal.reason, "breakout_close_above_swing_high")
        self.assertEqual(signal.entry_price, 114)
        # No retest happened, so the stop can only come from the entry bar.
        self.assertEqual(signal.stop_price, 108)
        self.assertEqual(signal.target_price, 114 + 2 * (114 - 108))
        self.assertIsNone(signal.retest_low)

    def test_without_trigger_the_retest_bar_signals(self):
        engine = BreakoutRetestEngine(require_trigger=False)

        _, events = feed(build([self.BREAKOUT, self.RETEST]), {5: rvol(2.0)}, engine)

        self.assertEqual([e.state for e in events], [WAIT_RETEST, SIGNAL])
        signal = events[1]
        self.assertEqual(signal.reason, "retest_accepted_no_trigger")
        self.assertEqual(signal.entry_price, 111)
        self.assertEqual(signal.stop_price, 110)

    def test_without_the_rvol_gate_a_missing_rvol_still_breaks_out(self):
        """The production rule blocks here; the ablation arm must not."""
        blocked = BreakoutRetestEngine()
        _, blocked_events = feed(build([self.BREAKOUT]), {}, blocked)
        self.assertEqual(blocked_events, [])

        engine = BreakoutRetestEngine(require_rvol=False)

        _, events = feed(build([self.BREAKOUT]), {}, engine)

        self.assertEqual([e.reason for e in events], ["breakout_confirmed"])
        self.assertIsNone(events[0].tod_rvol)

    def test_entry_bar_low_stop_applies_to_the_full_chain_too(self):
        """Same arm, different stop rule: the comparison needs one definition."""
        bars = build([self.BREAKOUT, self.RETEST, (118, 111, 117)])

        _, production = feed(bars, {5: rvol(2.0)}, BreakoutRetestEngine())
        _, ablation = feed(
            bars, {5: rvol(2.0)}, BreakoutRetestEngine(stop_rule=STOP_ENTRY_BAR_LOW)
        )

        self.assertEqual(production[-1].stop_price, 110)  # the retest low
        self.assertEqual(ablation[-1].stop_price, 111)  # the trigger bar low
        self.assertEqual(production[-1].entry_price, ablation[-1].entry_price)

    def test_a_bar_closing_on_its_low_has_no_risk_and_is_invalidated(self):
        engine = BreakoutRetestEngine(
            require_retest=False, require_trigger=False, stop_rule=STOP_ENTRY_BAR_LOW
        )

        _, events = feed(build([(115, 114, 114)]), {5: rvol(2.0)}, engine)

        self.assertEqual([e.state for e in events], [INVALIDATED])
        self.assertEqual(events[0].reason, "non_positive_risk")

    def test_fixed_tick_stop_scales_with_the_price_band(self):
        """The control has to be a distance, not a fixed number of dollars."""
        bars = build([self.BREAKOUT, self.RETEST, (118, 111, 117)])

        _, events = feed(
            bars,
            {5: rvol(2.0)},
            BreakoutRetestEngine(stop_rule=STOP_FIXED_TICKS, stop_ticks=4),
        )

        signal = events[-1]
        # Entry 117 is in the 100-500 band, so a tick is 0.50 and four is 2.00.
        self.assertEqual(signal.entry_price, 117)
        self.assertEqual(signal.stop_price, 115.0)
        self.assertEqual(signal.target_price, 117 + 2 * 2.0)

    def test_contradictory_combinations_are_refused(self):
        with self.assertRaises(ValueError):
            BreakoutRetestEngine(require_retest=False)  # trigger still required
        with self.assertRaises(ValueError):
            # The retest low does not exist once the retest is gone.
            BreakoutRetestEngine(require_retest=False, require_trigger=False)
        with self.assertRaises(ValueError):
            BreakoutRetestEngine(stop_rule="atr")
        with self.assertRaises(ValueError):
            BreakoutRetestEngine(stop_rule=STOP_FIXED_TICKS, stop_ticks=0)


class SummarizeExpectancyTest(unittest.TestCase):
    """PA-P9. The numbers that decide whether any of this is worth trading."""

    @staticmethod
    def _trade(r, cost_r=0.2, risk_ticks=6.0):
        return {"r": r, "cost_r": cost_r, "net_r": r - cost_r, "risk_ticks": risk_ticks}

    def test_expectancy_profit_factor_and_drawdown(self):
        trades = [self._trade(2.0), self._trade(-1.0), self._trade(-1.0), self._trade(2.0)]

        summary = summarize_expectancy(trades)

        gross = summary["gross"]
        self.assertEqual(summary["trades"], 4)
        self.assertEqual(gross["total_r"], 2.0)
        self.assertEqual(gross["expectancy_r"], 0.5)
        self.assertEqual(gross["win_rate"], 0.5)
        self.assertEqual(gross["average_win_r"], 2.0)
        self.assertEqual(gross["average_loss_r"], -1.0)
        self.assertEqual(gross["profit_factor"], 2.0)
        # Equity walks 2.0 -> 1.0 -> 0.0 -> 2.0, so the worst peak-to-trough is 2.0.
        self.assertEqual(gross["max_drawdown_r"], 2.0)

    def test_cost_moves_a_winner_into_a_loser(self):
        """The whole point of PA-P9: gross and net can disagree about the sign."""
        summary = summarize_expectancy([self._trade(0.5, cost_r=0.9)])

        self.assertEqual(summary["gross"]["total_r"], 0.5)
        self.assertEqual(summary["net"]["total_r"], -0.4)
        self.assertEqual(summary["gross"]["win_rate"], 1.0)
        self.assertEqual(summary["net"]["win_rate"], 0.0)

    def test_profit_factor_is_none_without_a_losing_trade(self):
        """No loss means the ratio is undefined, not infinite and not zero."""
        summary = summarize_expectancy([self._trade(2.0)])

        self.assertIsNone(summary["gross"]["profit_factor"])

    def test_empty_input_does_not_divide_by_zero(self):
        summary = summarize_expectancy([])

        self.assertEqual(summary["trades"], 0)
        self.assertEqual(summary["gross"]["expectancy_r"], 0.0)
        self.assertNotIn("cost_r", summary)


class PaperTradeCostTest(unittest.TestCase):
    """A trade must carry what it cost, not just what it moved."""

    def test_trade_reports_cost_and_net_r(self):
        report = run_paper_trading_day(
            trading_date=DATE,
            bars=winning_day(),
            rvol_by_key={f"2330|{DATE}T09:25:00": rvol(2.0)},
        )

        self.assertEqual(len(report["trades"]), 1)
        trade = report["trades"][0]
        self.assertGreater(trade["cost_r"], 0.0)
        self.assertAlmostEqual(trade["net_r"], trade["r"] - trade["cost_r"], places=9)
        # Entry 117 sits in the 100-500 band, so the tick is 0.50 and the 7 point
        # risk is 14 ticks of it.
        self.assertAlmostEqual(trade["risk_ticks"], 14.0, places=9)
        self.assertAlmostEqual(
            report["summary"]["net_total_r"],
            report["summary"]["total_r"] - trade["cost_r"],
            places=9,
        )
        self.assertEqual(report["expectancy"]["trades"], 1)


if __name__ == "__main__":
    unittest.main()

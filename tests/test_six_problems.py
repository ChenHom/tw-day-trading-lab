import unittest
import tempfile
import json
from unittest.mock import patch
from pathlib import Path
from tw_day_trading_lab.cost import TaiwanDayTradeCostModel
from tw_day_trading_lab.replay import ReplayAssumptions, replay_samples, check_exits
from tw_day_trading_lab.simulation import (
    check_pre_order_gates,
    build_simulation_plan,
    SignalIntent,
    RiskDecision,
    CandidateScore,
)
from tw_day_trading_lab.performance import RollingPerformanceTracker
from tw_day_trading_lab.strategy import VwapBreakoutStrategy, calculate_atr

class DummyBroker:
    def __init__(self, contract_details=None, is_sim=True):
        self.contract_details = contract_details or {}
        self._is_sim = is_sim

    def fetch_contract_details(self, symbol):
        return self.contract_details.get(symbol)

    def is_simulation(self):
        return self._is_sim


class SixProblemsTest(unittest.TestCase):
    def test_b1_cost_model_tick_sizes(self):
        model = TaiwanDayTradeCostModel()
        self.assertEqual(model.tick_size(5.0), 0.01)
        self.assertEqual(model.tick_size(25.0), 0.05)
        self.assertEqual(model.tick_size(80.0), 0.10)
        self.assertEqual(model.tick_size(300.0), 0.50)
        self.assertEqual(model.tick_size(750.0), 1.00)
        self.assertEqual(model.tick_size(1050.0), 5.00)

    def test_b1_cost_model_calculations(self):
        model = TaiwanDayTradeCostModel(commission_discount=0.3, slippage_ticks_per_side=1.0)
        # Entry = 300, Stop = 294, Quantity = 1000
        cost = model.round_trip_cost(300.0, 1000)
        # buy comm: 300 * 0.001425 * 0.3 = 0.12825
        # sell comm: 300 * 0.001425 * 0.3 = 0.12825
        # tax: 300 * 0.0015 = 0.45
        # slippage: 0.5 * 1.0 * 2 = 1.0
        # total per share: 0.12825 + 0.12825 + 0.45 + 1.0 = 1.7065
        self.assertAlmostEqual(cost, 1.7065, places=4)

        cost_r = model.cost_r(300.0, 294.0, 1000)
        # risk = 6.0
        # cost_r = 1.7065 / 6.0 = 0.2844
        self.assertAlmostEqual(cost_r, 0.2844, places=4)

    def test_b2_fixed_fractional_position_sizing(self):
        cand = CandidateScore(
            symbol="2330",
            name="台積電",
            rank=1,
            archetype="breakout",
            total_score=80.0,
            liquidity_score=90.0,
            event_score=80.0,
            structure_score=80.0,
            continuity_score=80.0,
            crowding_penalty=10.0,
            next_day_actionable=True,
            reasons=("liquid_enough",),
            downgrade_reasons=(),
            atr_20d_pct=2.0
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir)
            price_dir = cache_path / "finmind" / "TaiwanStockPrice" / "2026-06-02"
            price_dir.mkdir(parents=True, exist_ok=True)
            price_file = price_dir / "2330.jsonl"
            price_file.write_text(json.dumps({"close": 1000.0}) + "\n", encoding="utf-8")

            plan = build_simulation_plan(
                trading_date="2026-06-03",
                candidate_date="2026-06-02",
                candidates=[cand],
                cache_dir=cache_path,
                candidate_source="test",
                equity=1_000_000.0,
                risk_pct=0.01,
                stop_pct=0.01,
                use_fixed_fractional=True
            )
            self.assertTrue(plan[0].risk_decision.approved)
            self.assertEqual(plan[0].risk_decision.quantity, 1000)
            self.assertEqual(plan[0].quantity_source, "fixed_fractional")

    def test_b2_exit_triggers(self):
        bars = [
            {"high": 102, "low": 99, "time": "09:05"},
            {"high": 104, "low": 98, "time": "09:10"},
            {"high": 108, "low": 101, "time": "09:15"},
        ]

        # Stop loss trigger
        exit_price, reason = check_exits(
            entry_price=100.0,
            stop_price=98.0,
            target_price=110.0,
            price_bars=bars
        )
        self.assertEqual(reason, "stop_loss")
        self.assertEqual(exit_price, 98.0)

        # Take profit trigger
        exit_price, reason = check_exits(
            entry_price=100.0,
            stop_price=95.0,
            target_price=105.0,
            price_bars=bars
        )
        self.assertEqual(reason, "take_profit")
        self.assertEqual(exit_price, 105.0)

    def test_b3_microstructure_gates(self):
        broker = DummyBroker(contract_details={
            "2330": {
                "symbol": "2330",
                "reference": 1000.0,
                "limit_up": 1100.0,
                "limit_down": 900.0,
                "short_selling_eligible": False
            }
        })

        # 1. Buying at Limit Up is forbidden
        sig = SignalIntent("2026-06-03", "mvp", "2330", "breakout", "buy", 1000, 1100.0)
        dec = RiskDecision(approved=True, reason="ok", quantity=1000, price=1100.0)
        res = check_pre_order_gates(broker, sig, dec, current_time="09:30")
        self.assertFalse(res["approved"])
        self.assertIn("buying_at_limit_up_is_forbidden", res["blocked_reasons"])

        # 2. Short selling non-eligible contract is forbidden
        sig_sell = SignalIntent("2026-06-03", "mvp", "2330", "breakout", "sell", 1000, 950.0)
        dec_sell = RiskDecision(approved=True, reason="ok", quantity=1000, price=950.0)
        res_sell = check_pre_order_gates(broker, sig_sell, dec_sell, current_time="09:30")
        self.assertFalse(res_sell["approved"])
        self.assertIn("short_selling_not_eligible", res_sell["blocked_reasons"])

        # 3. 13:25-13:30 Special matching rule blocks
        sig_normal = SignalIntent("2026-06-03", "mvp", "2330", "breakout", "buy", 1000, 1050.0)
        dec_normal = RiskDecision(approved=True, reason="ok", quantity=1000, price=1050.0)
        res_time = check_pre_order_gates(broker, sig_normal, dec_normal, current_time="13:26")
        self.assertFalse(res_time["approved"])
        self.assertIn("final_match_collection_period", res_time["blocked_reasons"])

    def test_b5_performance_tracker_scaling(self):
        tracker = RollingPerformanceTracker(initial_equity=1_000_000.0)
        self.assertEqual(tracker.get_risk_multiplier(), 1.0)

        # Add 5 small losses so drawdown is small (0.5%) but rolling expectancy is negative
        for i in range(5):
            tracker.add_trade("TEST", 100, 99, -1.0, -1000.0)

        self.assertEqual(tracker.get_rolling_expectancy(), -1.0)
        # Should downsize to 0.5 because rolling expectancy is negative
        self.assertEqual(tracker.get_risk_multiplier(), 0.5)

        # Drawdown trigger: max drawdown limit of 1%
        # initial equity 1M, current equity 995K (drawdown = 0.5%)
        self.assertEqual(tracker.get_current_drawdown(), 0.005)
        self.assertEqual(tracker.get_risk_multiplier(max_drawdown_limit=0.004), 0.0)

    def test_b6_vwap_breakout_strategy(self):
        strategy = VwapBreakoutStrategy(observation_minutes=2, volume_surge_ratio=1.5)
        # First 2 bars define opening range: high = 100, low = 98, avg volume = 1000
        bars = [
            {"high": 99, "low": 98, "Trading_Volume": 1000, "close": 99, "time": "09:01"},
            {"high": 100, "low": 98.5, "Trading_Volume": 1000, "close": 99.5, "time": "09:02"},
            {"high": 102, "low": 99.5, "Trading_Volume": 2000, "close": 101.5, "time": "09:03"},
        ]
        sig = strategy.generate_signal("2330", bars)
        self.assertTrue(sig.triggered)
        self.assertEqual(sig.entry_price, 101.5)
        self.assertEqual(sig.stop_price, 98.0)
        self.assertAlmostEqual(sig.target_price, 101.5 + 2 * (100 - 98.0))

    def test_b4_confidence_intervals(self):
        samples = [
            {
                "sample_id": "valid-1",
                "validity": "valid",
                "idempotency_key": "2026-05-28:mvp:2330:setup:buy",
                "symbol": "2330",
                "entry_price": 100,
                "exit_price": 102,
                "stop_price": 99,
            },
            {
                "sample_id": "valid-2",
                "validity": "valid",
                "idempotency_key": "2026-05-28:mvp:3017:setup:buy",
                "symbol": "3017",
                "entry_price": 100,
                "exit_price": 105,
                "stop_price": 99,
            }
        ]
        result = replay_samples(samples, assumptions=ReplayAssumptions(cost_r=0.1))
        # net R for trade 1: (102-100)/1 - 0.1 = 1.9R
        # net R for trade 2: (105-100)/1 - 0.1 = 4.9R
        # expectancy net R = 3.4R
        summary = result.summary
        self.assertEqual(summary["expectancy_net_r"], 3.4)
        self.assertIsNotNone(summary["net_expectancy_ci_lower"])
        self.assertIsNotNone(summary["net_expectancy_ci_upper"])
        self.assertIn("N=2 < 30", summary["statistical_warning"])


if __name__ == "__main__":
    unittest.main()

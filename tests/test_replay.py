import unittest

from tw_day_trading_lab.replay import (
    ReplayAssumptions,
    replay_samples,
    render_replay_markdown,
)


class ReplayTest(unittest.TestCase):
    def test_replay_uses_only_valid_samples_for_expectancy(self):
        samples = [
            {
                "sample_id": "valid-1",
                "validity": "valid",
                "idempotency_key": "2026-05-28:mvp:2330:setup:buy",
                "symbol": "2330",
                "entry_price": 100,
                "exit_price": 110,
                "stop_price": 95,
            },
            {
                "sample_id": "excluded-1",
                "validity": "excluded",
                "idempotency_key": "2026-05-28:mvp:9999:setup:buy",
                "symbol": "9999",
                "entry_price": 100,
                "exit_price": 200,
                "stop_price": 95,
            },
        ]

        result = replay_samples(samples, assumptions=ReplayAssumptions(cost_r=0.1))

        self.assertEqual(result.summary["total_samples"], 2)
        self.assertEqual(result.summary["replayed"], 1)
        self.assertEqual(result.summary["skipped_excluded"], 1)
        self.assertEqual(result.summary["expectancy_gross_r"], 2.0)
        self.assertEqual(result.summary["expectancy_net_r"], 1.9)

    def test_replay_computes_mfe_mae_and_net_r_from_price_bars(self):
        samples = [
            {
                "sample_id": "valid-1",
                "validity": "valid",
                "idempotency_key": "2026-05-28:mvp:2330:setup:buy",
                "symbol": "2330",
                "entry_price": 100,
                "exit_price": 108,
                "stop_price": 96,
            }
        ]
        bars = {
            "2330": [
                {"high": 103, "low": 99},
                {"high": 112, "low": 94},
                {"high": 109, "low": 101},
            ]
        }

        result = replay_samples(samples, price_bars_by_symbol=bars, assumptions=ReplayAssumptions(cost_r=0.25))
        trade = result.trades[0]

        self.assertEqual(trade.realized_r_gross, 2.0)
        self.assertEqual(trade.estimated_cost_r, 0.25)
        self.assertEqual(trade.realized_r_net, 1.75)
        self.assertEqual(trade.mfe_r, 3.0)
        self.assertEqual(trade.mae_r, -1.5)

    def test_replay_skips_duplicate_idempotency_keys(self):
        samples = [
            {
                "sample_id": "valid-1",
                "validity": "valid",
                "idempotency_key": "2026-05-28:mvp:2330:setup:buy",
                "symbol": "2330",
                "entry_price": 100,
                "exit_price": 110,
                "stop_price": 95,
            },
            {
                "sample_id": "valid-duplicate",
                "validity": "valid",
                "idempotency_key": "2026-05-28:mvp:2330:setup:buy",
                "symbol": "2330",
                "entry_price": 100,
                "exit_price": 80,
                "stop_price": 95,
            },
        ]

        result = replay_samples(samples)

        self.assertEqual(result.summary["replayed"], 1)
        self.assertEqual(result.summary["skipped_duplicate_idempotency"], 1)
        self.assertEqual(result.trades[0].sample_id, "valid-1")

    def test_replay_markdown_splits_gross_cost_and_net_r(self):
        result = replay_samples(
            [
                {
                    "sample_id": "valid-1",
                    "validity": "valid",
                    "idempotency_key": "2026-05-28:mvp:2330:setup:buy",
                    "symbol": "2330",
                    "entry_price": 100,
                    "exit_price": 110,
                    "stop_price": 95,
                }
            ],
            assumptions=ReplayAssumptions(cost_r=0.1),
        )

        report = render_replay_markdown("2026-05-28", result)

        self.assertIn("Gross R", report)
        self.assertIn("Cost R", report)
        self.assertIn("Net R", report)
        self.assertIn("validity = valid", report)


if __name__ == "__main__":
    unittest.main()

import unittest

from tw_day_trading_lab.ledger import DuplicateIntentError, OrderIntent, PaperLedger


class PaperLedgerTest(unittest.TestCase):
    def test_rejects_duplicate_open_lifecycle(self):
        ledger = PaperLedger()
        intent = OrderIntent(
            trading_date="2026-05-28",
            strategy_id="mvp",
            symbol="2330",
            setup_id="breakout",
            side="buy",
        )

        ledger.register_intent(intent)

        with self.assertRaises(DuplicateIntentError):
            ledger.register_intent(intent)

    def test_can_reopen_after_close(self):
        ledger = PaperLedger()
        intent = OrderIntent(
            trading_date="2026-05-28",
            strategy_id="mvp",
            symbol="2330",
            setup_id="breakout",
            side="buy",
        )

        first_key = ledger.register_intent(intent)
        ledger.close_intent(intent)
        second_key = ledger.register_intent(intent)

        self.assertEqual(first_key, second_key)


if __name__ == "__main__":
    unittest.main()


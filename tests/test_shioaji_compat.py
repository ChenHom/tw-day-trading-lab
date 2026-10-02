import unittest
from types import SimpleNamespace

from tw_day_trading_lab.shioaji_compat import (
    login_simulation_api,
    shioaji_enum_value,
    stock_contract,
)


class ShioajiCompatibilityTest(unittest.TestCase):
    def test_enum_value_prefers_new_top_level_enum(self):
        sdk = SimpleNamespace(
            Action=SimpleNamespace(Buy="new-buy"),
            constant=SimpleNamespace(Action=SimpleNamespace(Buy="legacy-buy")),
        )

        self.assertEqual(shioaji_enum_value(sdk, "Action", "Buy"), "new-buy")

    def test_enum_value_supports_legacy_constant_namespace(self):
        sdk = SimpleNamespace(
            constant=SimpleNamespace(Action=SimpleNamespace(Buy="legacy-buy")),
        )

        self.assertEqual(shioaji_enum_value(sdk, "Action", "Buy"), "legacy-buy")

    def test_stock_contract_supports_legacy_mapping(self):
        api = SimpleNamespace(Contracts=SimpleNamespace(Stocks={"2330": "legacy"}))

        self.assertEqual(stock_contract(api, "2330"), "legacy")

    def test_stock_contract_prefers_new_service(self):
        stocks = SimpleNamespace(get=lambda symbol: f"new-{symbol}")
        api = SimpleNamespace(
            contracts=SimpleNamespace(stocks=stocks),
            Contracts=SimpleNamespace(Stocks={"2330": "legacy"}),
        )

        self.assertEqual(stock_contract(api, "2330"), "new-2330")

    def test_login_omits_removed_fetch_contract_keyword(self):
        calls = []

        def login(*, api_key, secret_key, subscribe_trade):
            calls.append(locals())
            return ["stock-account"]

        accounts = login_simulation_api(
            login,
            api_key="key",
            secret_key="secret",
            fetch_contract=False,
            subscribe_trade=False,
        )

        self.assertEqual(accounts, ["stock-account"])
        self.assertNotIn("fetch_contract", calls[0])

    def test_login_preserves_optional_keywords_for_legacy_api(self):
        calls = []

        def login(**kwargs):
            calls.append(kwargs)
            return []

        login_simulation_api(
            login,
            api_key="key",
            secret_key="secret",
            fetch_contract=True,
            subscribe_trade=True,
        )

        self.assertTrue(calls[0]["fetch_contract"])
        self.assertTrue(calls[0]["subscribe_trade"])

    def test_login_never_silently_drops_required_credentials(self):
        def login(*, subscribe_trade):
            return []

        with self.assertRaisesRegex(TypeError, "required credentials"):
            login_simulation_api(
                login,
                api_key="key",
                secret_key="secret",
                fetch_contract=False,
                subscribe_trade=False,
            )


if __name__ == "__main__":
    unittest.main()

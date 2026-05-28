from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderIntent:
    trading_date: str
    strategy_id: str
    symbol: str
    setup_id: str
    side: str

    @property
    def idempotency_key(self) -> str:
        return ":".join(
            [
                self.trading_date,
                self.strategy_id,
                self.symbol,
                self.setup_id,
                self.side.lower(),
            ]
        )


class DuplicateIntentError(ValueError):
    pass


class PaperLedger:
    def __init__(self) -> None:
        self._open_keys: set[str] = set()
        self._open_symbol_setups: set[tuple[str, str, str, str]] = set()

    def register_intent(self, intent: OrderIntent) -> str:
        key = intent.idempotency_key
        symbol_setup_key = (
            intent.trading_date,
            intent.strategy_id,
            intent.symbol,
            intent.setup_id,
        )
        if key in self._open_keys or symbol_setup_key in self._open_symbol_setups:
            raise DuplicateIntentError(f"duplicate open lifecycle: {key}")

        self._open_keys.add(key)
        self._open_symbol_setups.add(symbol_setup_key)
        return key

    def close_intent(self, intent: OrderIntent) -> None:
        self._open_keys.discard(intent.idempotency_key)
        self._open_symbol_setups.discard(
            (intent.trading_date, intent.strategy_id, intent.symbol, intent.setup_id)
        )


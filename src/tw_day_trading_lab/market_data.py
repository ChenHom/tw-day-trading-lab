"""Market data provider boundary: Shioaji tick -> normalized MarketTick.

P1 scope only. This module must not import the Shioaji SDK: adapters take a
duck-typed api object so unit tests run without the SDK installed and the
strategy layer never depends on a provider payload shape.

Tick -> 1m bar aggregation is P2 and deliberately not implemented here.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SHARES_PER_LOT = 1000
TICK_SOURCE = "shioaji_tick"
RAW_TICK_PROVIDER = "shioaji"
RAW_TICK_DATASET = "ticks"


@dataclass(frozen=True)
class MarketTick:
    """One normalized trade tick. Volumes are always in shares, never lots."""

    symbol: str
    exchange: str
    timestamp: str
    price: float
    trade_volume: int
    cumulative_volume: int
    source: str
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_shioaji_tick(
    exchange: Any,
    tick: Any,
    *,
    sequence: int,
) -> tuple[MarketTick | None, str]:
    """Normalize one Shioaji stock tick.

    Returns `(market_tick, "")` when accepted, or `(None, reason)` when the tick
    must not reach the aggregator. Rejected ticks are never an error: simtrade
    prices are not real deals, and odd-lot volumes use a different unit.
    """
    if getattr(tick, "simtrade", False):
        return None, "simtrade"
    if getattr(tick, "intraday_odd", False):
        return None, "intraday_odd"
    if getattr(tick, "suspend", False):
        return None, "suspend"

    symbol = str(getattr(tick, "code", "") or "")
    timestamp = _timestamp_text(getattr(tick, "datetime", None))
    price = _to_float(getattr(tick, "close", None))
    if not symbol or not timestamp or price is None:
        return None, "needs_review"

    # Shioaji regular-lot tick volumes are in lots (K shares); MarketTick is shares.
    return (
        MarketTick(
            symbol=symbol,
            exchange=_exchange_value(exchange),
            timestamp=timestamp,
            price=price,
            trade_volume=_to_int(getattr(tick, "volume", 0)) * SHARES_PER_LOT,
            cumulative_volume=_to_int(getattr(tick, "total_volume", 0)) * SHARES_PER_LOT,
            source=TICK_SOURCE,
            sequence=sequence,
        ),
        "",
    )


def raw_tick_path(cache_dir: Path, trading_date: str, symbol: str) -> Path:
    """Return the raw tick JSONL path for one trading date and symbol."""
    return cache_dir / RAW_TICK_PROVIDER / RAW_TICK_DATASET / trading_date / f"{symbol}.jsonl"


def append_raw_ticks(
    cache_dir: Path,
    trading_date: str,
    symbol: str,
    rows: Sequence[Mapping[str, Any]],
) -> Path:
    """Append raw provider tick payloads as JSONL for audit and rebuild."""
    path = raw_tick_path(cache_dir, trading_date, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: append instead of finmind_ingestion.write_jsonl, which rewrites the
    # whole file; a tick stream would make that O(n^2) over a session.
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    return path


class ShioajiTickStream:
    """Subscribe candidate-scoped Shioaji ticks and emit normalized MarketTick.

    Only the supplied candidate symbols are subscribed; this stream never scans
    the whole market. Like the order gateway, it refuses a non-simulation api.
    """

    def __init__(
        self,
        api: Any,
        *,
        trading_date: str,
        symbols: Sequence[str],
        sink: Callable[[MarketTick], None] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        if getattr(api, "simulation", True) is not True:
            raise ValueError("ShioajiTickStream requires api.simulation=True")
        self._api = api
        self._trading_date = trading_date
        self._symbols = [str(symbol) for symbol in symbols]
        self._sink = sink
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._subscribed: list[Any] = []
        self._sequence: dict[str, int] = {}
        self._last_cumulative: dict[str, int] = {}
        self.raw_count = 0
        self.accepted_count = 0
        self.subscribed_count = 0
        self.out_of_order = 0
        self.rejected: Counter[str] = Counter()

    def start(self) -> None:
        self._api.quote.set_on_tick_stk_v1_callback(self.handle_tick)
        for symbol in self._symbols:
            try:
                contract = self._api.Contracts.Stocks[symbol]
            except Exception:
                contract = None
            if contract is None:
                self.rejected["contract_not_found"] += 1
                continue
            # ponytail: SDK defaults are already QuoteType.Tick / v1 / intraday_odd=False,
            # so we skip the enum import and keep this module free of the SDK.
            self._api.quote.subscribe(contract)
            self._subscribed.append(contract)
            self.subscribed_count += 1

    def stop(self) -> None:
        for contract in self._subscribed:
            self._api.quote.unsubscribe(contract)
        self._subscribed.clear()

    def handle_tick(self, exchange: Any, tick: Any) -> MarketTick | None:
        """Provider callback: persist the raw payload, then normalize."""
        self.raw_count += 1
        symbol = str(getattr(tick, "code", "") or "")
        if self._cache_dir is not None and symbol:
            append_raw_ticks(
                self._cache_dir,
                self._trading_date,
                symbol,
                [raw_tick_payload(exchange, tick)],
            )

        sequence = self._sequence.get(symbol, 0)
        market_tick, reason = normalize_shioaji_tick(exchange, tick, sequence=sequence)
        if market_tick is None:
            self.rejected[reason] += 1
            return None

        self._sequence[symbol] = sequence + 1
        self.accepted_count += 1
        last_cumulative = self._last_cumulative.get(symbol)
        if last_cumulative is not None and market_tick.cumulative_volume < last_cumulative:
            self.out_of_order += 1
        else:
            self._last_cumulative[symbol] = market_tick.cumulative_volume

        if self._sink is not None:
            self._sink(market_tick)
        return market_tick

    def summary(self) -> dict[str, Any]:
        return {
            "symbols": list(self._symbols),
            # Counted at subscribe time so the summary survives stop().
            "subscribed": self.subscribed_count,
            "raw_ticks": self.raw_count,
            "market_ticks": self.accepted_count,
            "out_of_order": self.out_of_order,
            "rejected": dict(self.rejected),
        }


def run_gated_shioaji_tick_stream_smoke(
    *,
    api: Any,
    trading_date: str,
    symbols: Sequence[str],
    enabled: bool,
    credentials_present: bool = True,
    cache_dir: Path | None = None,
    duration_seconds: float = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Subscribe candidate ticks behind an explicit gate; never places orders."""
    symbol_list = [str(symbol) for symbol in symbols]
    report: dict[str, Any] = {
        "status": "blocked",
        "mode": "shioaji_tick_stream",
        "trading_date": trading_date,
        "checks": {
            "gate_enabled": enabled,
            "credentials_present": credentials_present,
            "simulation_api": getattr(api, "simulation", None) is True,
            "candidate_scoped": True,
            "symbol_count": len(symbol_list),
            "orders_allowed": False,
        },
        "side_effects": [],
        "review_reason": "",
    }
    if not enabled:
        report["review_reason"] = "enable_tick_stream_required"
        return report
    if not credentials_present:
        report["review_reason"] = "shioaji_credentials_required"
        return report
    if not symbol_list:
        report["review_reason"] = "candidate_symbols_required"
        return report

    report["side_effects"] = ["quote_subscribe"]
    stream = ShioajiTickStream(
        api=api,
        trading_date=trading_date,
        symbols=symbol_list,
        cache_dir=cache_dir,
    )
    stream.start()
    if duration_seconds > 0:
        sleep(duration_seconds)
    stream.stop()
    report["status"] = "ok"
    report["summary"] = stream.summary()
    return report


def raw_tick_payload(exchange: Any, tick: Any) -> dict[str, Any]:
    """Build a JSON-safe audit copy of the provider tick, including simtrade rows."""
    keys = set(getattr(type(tick), "__annotations__", {}))
    keys |= set(vars(tick)) if hasattr(tick, "__dict__") else set()
    payload = {key: _json_safe(getattr(tick, key, None)) for key in sorted(keys)}
    payload["exchange"] = _exchange_value(exchange)
    return payload


def _exchange_value(exchange: Any) -> str:
    return str(getattr(exchange, "value", exchange) or "")


def _timestamp_text(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(getattr(value, "value", value))

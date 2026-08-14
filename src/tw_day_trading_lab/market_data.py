"""Market data provider boundary: Shioaji tick -> normalized MarketTick.

P1 scope only. This module must not import the Shioaji SDK: adapters take a
duck-typed api object so unit tests run without the SDK installed and the
strategy layer never depends on a provider payload shape.

The provider callback only enqueues; every parse, dedupe and disk write runs on
a consumer thread. Blocking a quote callback delays the whole tick feed, which
would silently corrupt every volume-derived feature downstream.

Tick -> 1m bar aggregation is P2 and deliberately not implemented.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable, Mapping, Sequence

SHARES_PER_LOT = 1000
TICK_SOURCE = "shioaji_tick"
RAW_TICK_PROVIDER = "shioaji"
RAW_TICK_DATASET = "ticks"
UNKNOWN_SYMBOL = "_unknown"
TICK_QUEUE_MAXSIZE = 100_000
DEDUPE_WINDOW = 512


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
    # Shioaji regular-lot tick volumes are in lots (K shares); MarketTick is shares.
    trade_volume = _to_int_or_none(getattr(tick, "volume", None))
    cumulative_volume = _to_int_or_none(getattr(tick, "total_volume", None))

    # A broken volume must never be reported as "this minute had no volume":
    # every RVOL / breakout feature downstream would read it as real.
    if not symbol or not timestamp or price is None:
        return None, "needs_review"
    if trade_volume is None or cumulative_volume is None:
        return None, "needs_review"
    if trade_volume < 0 or cumulative_volume < 0:
        return None, "needs_review"

    return (
        MarketTick(
            symbol=symbol,
            exchange=_exchange_value(exchange),
            timestamp=timestamp,
            price=price,
            trade_volume=trade_volume * SHARES_PER_LOT,
            cumulative_volume=cumulative_volume * SHARES_PER_LOT,
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

    Only the supplied candidate symbols are subscribed and only their ticks are
    ingested; a quote callback is shared per api object, so subscription scope
    alone does not bound what arrives. Like the order gateway, this stream
    refuses a non-simulation api.
    """

    def __init__(
        self,
        api: Any,
        *,
        trading_date: str,
        symbols: Sequence[str],
        sink: Callable[[MarketTick], None] | None = None,
        cache_dir: Path | None = None,
        queue_maxsize: int = TICK_QUEUE_MAXSIZE,
    ) -> None:
        if getattr(api, "simulation", True) is not True:
            raise ValueError("ShioajiTickStream requires api.simulation=True")
        self._api = api
        self._trading_date = trading_date
        self._symbols = [str(symbol) for symbol in symbols]
        self._symbol_set = frozenset(self._symbols)
        self._sink = sink
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._queue: Queue = Queue(maxsize=queue_maxsize)
        self._subscribed: list[Any] = []
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self._sequence: dict[str, int] = {}
        self._seen: dict[str, dict[tuple[str, int], None]] = {}
        self._last_cumulative: dict[str, int] = {}
        self.raw_count = 0
        self.accepted_count = 0
        self.subscribed_count = 0
        self.out_of_order = 0
        self.dropped_queue_full = 0
        self.rejected: Counter[str] = Counter()

    # -- provider callback -------------------------------------------------

    def handle_tick(self, exchange: Any, tick: Any) -> None:
        """Provider callback. Enqueue and return; never touch disk here."""
        try:
            self._queue.put_nowait((exchange, tick))
        except Full:
            # Visible data loss beats a blocked feed: the summary reports it.
            self.dropped_queue_full += 1

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        _set_tick_callback(self._api, self.handle_tick)
        try:
            for symbol in self._symbols:
                try:
                    contract = self._api.Contracts.Stocks[symbol]
                except Exception:
                    contract = None
                if contract is None:
                    self.rejected["contract_not_found"] += 1
                    continue
                _subscribe(self._api, contract)
                self._subscribed.append(contract)
                self.subscribed_count += 1
        except Exception:
            self._unsubscribe_all()
            raise

        self._stopping.clear()
        self._worker = threading.Thread(
            target=self._run_worker,
            name="shioaji-tick-worker",
            daemon=True,
        )
        self._worker.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        """Always unsubscribe, then stop the worker and flush what is queued."""
        try:
            self._unsubscribe_all()
        finally:
            self._stopping.set()
            worker, self._worker = self._worker, None
            if worker is not None:
                worker.join(timeout=join_timeout)
            self.drain()

    def _unsubscribe_all(self) -> None:
        for contract in self._subscribed:
            try:
                _unsubscribe(self._api, contract)
            except Exception:
                self.rejected["unsubscribe_failed"] += 1
        self._subscribed.clear()

    # -- consumer ----------------------------------------------------------

    def _run_worker(self) -> None:
        while not self._stopping.is_set():
            try:
                exchange, tick = self._queue.get(timeout=0.2)
            except Empty:
                continue
            self._process(exchange, tick)

    def drain(self) -> int:
        """Process everything currently queued and return how many were handled.

        ponytail: used by tests and by the final flush in stop(); do not call it
        concurrently with a running worker.
        """
        processed = 0
        while True:
            try:
                exchange, tick = self._queue.get_nowait()
            except Empty:
                return processed
            self._process(exchange, tick)
            processed += 1

    def _process(self, exchange: Any, tick: Any) -> MarketTick | None:
        self.raw_count += 1
        symbol = str(getattr(tick, "code", "") or "")

        # The quote callback is shared per api object, so another subscriber's
        # symbols can arrive here. They are not our data.
        if symbol and symbol not in self._symbol_set:
            self.rejected["outside_candidate_scope"] += 1
            return None

        if self._cache_dir is not None:
            # A tick with no code is exactly the one worth investigating later,
            # so it gets an audit bucket instead of being dropped silently.
            append_raw_ticks(
                self._cache_dir,
                self._trading_date,
                symbol or UNKNOWN_SYMBOL,
                [raw_tick_payload(exchange, tick)],
            )

        sequence = self._sequence.get(symbol, 0)
        market_tick, reason = normalize_shioaji_tick(exchange, tick, sequence=sequence)
        if market_tick is None:
            self.rejected[reason] += 1
            return None

        dedupe_key = (market_tick.timestamp, market_tick.cumulative_volume)
        if not self._remember(symbol, dedupe_key):
            # A retransmitted tick would be added again by the P2 aggregator.
            self.rejected["duplicate"] += 1
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

    def _remember(self, symbol: str, key: tuple[str, int]) -> bool:
        """Return False when this key was already seen inside the recent window."""
        seen = self._seen.setdefault(symbol, {})
        if key in seen:
            return False
        seen[key] = None
        # ponytail: bounded recent window instead of a whole-session set, which
        # would grow with every tick. Widen DEDUPE_WINDOW if real retransmits
        # ever arrive further apart than this.
        if len(seen) > DEDUPE_WINDOW:
            seen.pop(next(iter(seen)))
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "symbols": list(self._symbols),
            # Counted at subscribe time so the summary survives stop().
            "subscribed": self.subscribed_count,
            "raw_ticks": self.raw_count,
            "market_ticks": self.accepted_count,
            "out_of_order": self.out_of_order,
            "dropped_queue_full": self.dropped_queue_full,
            "queue_backlog": self._queue.qsize(),
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
    try:
        stream.start()
        if duration_seconds > 0:
            sleep(duration_seconds)
    finally:
        # Shioaji caps concurrent connections per person_id, so subscriptions
        # must be released even when the observation window blows up.
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


def _set_tick_callback(api: Any, func: Callable[[Any, Any], None]) -> None:
    """Register the stock tick callback across Shioaji API generations."""
    setter = getattr(api, "set_on_tick_stk_v1_callback", None)
    if setter is None:
        setter = api.quote.set_on_tick_stk_v1_callback
    setter(func)


def _subscribe(api: Any, contract: Any) -> None:
    # ponytail: SDK defaults are already QuoteType.Tick / v1 / intraday_odd=False,
    # so we skip the enum import and keep this module free of the SDK.
    subscribe = getattr(api, "subscribe", None)
    if subscribe is None:
        subscribe = api.quote.subscribe
    subscribe(contract)


def _unsubscribe(api: Any, contract: Any) -> None:
    unsubscribe = getattr(api, "unsubscribe", None)
    if unsubscribe is None:
        unsubscribe = api.quote.unsubscribe
    unsubscribe(contract)


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


def _to_int_or_none(value: Any) -> int | None:
    """Parse an integer field, returning None when it is missing or unusable."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(getattr(value, "value", value))

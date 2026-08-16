"""Canonical bar aggregation: MarketTick -> 1m MarketBar.

P2 scope. Provider-agnostic on purpose: the only input is `MarketTick`, so
live Shioaji ticks, synthetic fixtures and historical replay all run through
exactly the same aggregation logic.

Two decisions worth stating up front:

- **No synthetic bars.** A minute with no trades produces no bar. "Real zero
  volume", "nobody traded" and "the feed dropped data" are three different
  facts and the canonical layer must not merge them: a bar with volume 0 is
  the first, a missing bar is the second, and the third is what market-data
  health in `market_data.py` reports. A later normalization layer may decide
  to pad gaps; this one records them.
- **Time is event time.** The watermark advances from tick timestamps, never
  from the wall clock, so a replay produces byte-identical bars to a live
  session. A live caller that needs bars to close while nothing is trading
  calls `flush(now=...)` with its own clock.

1m -> 5m aggregation is P3 and deliberately not implemented.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from .market_data import MarketTick

TIMEFRAME_1M = "1m"
TIMEFRAME_5M = "5m"
BAR_SOURCE_1M = "shioaji_tick_aggregated"
BAR_SOURCE_5M = "local_1m_aggregated"
FIVE_MINUTE_BUCKET = 5
DEFAULT_5M_CORRECTION_WINDOW_MINUTES = 30

STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"
STATUS_CORRECTED = "CORRECTED"

DEFAULT_LATENESS_SECONDS = 3.0
CORRECTION_WINDOW_MINUTES = 10


@dataclass(frozen=True)
class MarketBar:
    """One canonical bar. Immutable, so a stored revision can never change."""

    symbol: str
    timeframe: str
    start_at: str
    end_at: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_count: int
    status: str
    revision: int
    source: str
    sequence_gap: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _BarState:
    """Mutable accumulator; only `to_bar()` output ever leaves the aggregator."""

    symbol: str
    start: datetime
    first_at: datetime
    last_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    trade_count: int = 0
    revision: int = 1
    sequence_gap: bool = False

    def apply(self, tick: MarketTick, at: datetime) -> None:
        # Open and close follow event time, not arrival order: a late tick
        # must not become the close just because it showed up last.
        if at < self.first_at:
            self.first_at = at
            self.open = tick.price
        if at >= self.last_at:
            self.last_at = at
            self.close = tick.price
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.volume += tick.trade_volume
        self.trade_count += 1

    def to_bar(self, status: str) -> MarketBar:
        return MarketBar(
            symbol=self.symbol,
            timeframe=TIMEFRAME_1M,
            start_at=self.start.isoformat(),
            end_at=(self.start + timedelta(minutes=1)).isoformat(),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            trade_count=self.trade_count,
            status=status,
            revision=self.revision,
            source=BAR_SOURCE_1M,
            sequence_gap=self.sequence_gap,
        )


@dataclass
class _SymbolState:
    pending: dict[datetime, _BarState] = field(default_factory=dict)
    closed: dict[datetime, _BarState] = field(default_factory=dict)
    last_minute: datetime | None = None
    last_sequence: int | None = None


class OneMinuteBarAggregator:
    """Aggregate MarketTick into canonical 1m bars.

    Buckets are half-open on exchange local time: `[09:00:00, 09:01:00)`.

    A bar is not finalized the moment its minute ends. It stays pending for
    `lateness_seconds` so a tick stamped 09:00:59.8 that arrives at 09:01:01
    still lands in its own minute instead of forcing a correction. Once the
    watermark passes `end + lateness` the bar is emitted as CLOSED. A tick
    that arrives after that, but still inside the correction window, produces
    a CORRECTED bar with the next revision; the already-emitted revision is a
    frozen object and stays exactly as the strategy saw it.

    Ticks must be fed in roughly non-decreasing timestamp order across
    symbols. Anything older than the correction window is counted as
    `dropped_late` rather than silently applied.
    """

    def __init__(
        self,
        *,
        lateness_seconds: float = DEFAULT_LATENESS_SECONDS,
        correction_window_minutes: int = CORRECTION_WINDOW_MINUTES,
    ) -> None:
        self._lateness = timedelta(seconds=lateness_seconds)
        self._correction_window = timedelta(minutes=correction_window_minutes)
        self._symbols: dict[str, _SymbolState] = {}
        self._watermark: datetime | None = None
        self._pending_gap = False
        self.tick_count = 0
        self.bars_closed = 0
        self.bars_corrected = 0
        self.late_ticks = 0
        self.dropped_late = 0
        self.sequence_gaps = 0
        self.no_trade_minutes = 0
        self.invalid_timestamps = 0

    # -- ingestion ---------------------------------------------------------

    def on_tick(self, tick: MarketTick) -> list[MarketBar]:
        """Apply one tick and return the bars finalized as a result."""
        at = _parse_timestamp(tick.timestamp)
        if at is None:
            self.invalid_timestamps += 1
            return []

        self.tick_count += 1
        if self._watermark is None or at > self._watermark:
            self._watermark = at

        state = self._symbols.setdefault(tick.symbol, _SymbolState())
        self._track_sequence(state, tick)

        minute = at.replace(second=0, microsecond=0)
        bar = state.pending.get(minute)
        if bar is not None:
            bar.apply(tick, at)
            bar.sequence_gap = bar.sequence_gap or self._pending_gap
            return self._finalize_due()

        closed = state.closed.get(minute)
        if closed is not None:
            # The bar was already emitted: correct it and bump the revision.
            self.late_ticks += 1
            closed.apply(tick, at)
            closed.sequence_gap = closed.sequence_gap or self._pending_gap
            closed.revision += 1
            self.bars_corrected += 1
            corrected = closed.to_bar(STATUS_CORRECTED)
            return [corrected, *self._finalize_due()]

        if self._watermark - minute > self._correction_window:
            # Too old to attribute to anything we still hold.
            self.dropped_late += 1
            return []

        if minute + timedelta(minutes=1) + self._lateness <= self._watermark:
            # Late tick for a minute we never opened; still counted as late.
            self.late_ticks += 1

        self._open_bar(state, tick, at, minute)
        return self._finalize_due()

    def flush(self, *, now: str | datetime | None = None) -> list[MarketBar]:
        """Finalize whatever the given clock makes due, without new ticks."""
        if now is not None:
            at = now if isinstance(now, datetime) else _parse_timestamp(now)
            if at is not None and (self._watermark is None or at > self._watermark):
                self._watermark = at
        return self._finalize_due()

    def close_all(self) -> list[MarketBar]:
        """Session end: finalize every pending bar regardless of lateness."""
        bars = []
        for symbol, state in self._symbols.items():
            for minute in sorted(state.pending):
                bars.append((minute, symbol, state.pending.pop(minute)))
        return self._emit(bars)

    # -- internals ---------------------------------------------------------

    def _track_sequence(self, state: _SymbolState, tick: MarketTick) -> None:
        self._pending_gap = False
        if state.last_sequence is not None and tick.sequence != state.last_sequence + 1:
            # P1 already reports the loss; P2 marks which bar it touched.
            self._pending_gap = True
            self.sequence_gaps += 1
        state.last_sequence = tick.sequence

    def _open_bar(
        self,
        state: _SymbolState,
        tick: MarketTick,
        at: datetime,
        minute: datetime,
    ) -> None:
        if state.last_minute is not None and minute > state.last_minute:
            gap = int((minute - state.last_minute).total_seconds() // 60) - 1
            if gap > 0:
                # No bar is emitted for these minutes; only the count is kept.
                self.no_trade_minutes += gap
        if state.last_minute is None or minute > state.last_minute:
            state.last_minute = minute
        state.pending[minute] = _BarState(
            symbol=tick.symbol,
            start=minute,
            first_at=at,
            last_at=at,
            open=tick.price,
            high=tick.price,
            low=tick.price,
            close=tick.price,
            volume=tick.trade_volume,
            trade_count=1,
            sequence_gap=self._pending_gap,
        )

    def _finalize_due(self) -> list[MarketBar]:
        if self._watermark is None:
            return []
        due = []
        for symbol, state in self._symbols.items():
            for minute in sorted(state.pending):
                if minute + timedelta(minutes=1) + self._lateness <= self._watermark:
                    due.append((minute, symbol, state.pending.pop(minute)))
        return self._emit(due)

    def _emit(self, due: list[tuple[datetime, str, _BarState]]) -> list[MarketBar]:
        bars = []
        for minute, symbol, bar_state in sorted(due, key=lambda item: (item[0], item[1])):
            state = self._symbols[symbol]
            state.closed[minute] = bar_state
            self._prune_closed(state)
            self.bars_closed += 1
            bars.append(bar_state.to_bar(STATUS_CLOSED))
        return bars

    def _prune_closed(self, state: _SymbolState) -> None:
        if self._watermark is None:
            return
        cutoff = self._watermark - self._correction_window
        for minute in [m for m in state.closed if m < cutoff]:
            del state.closed[minute]

    # -- reporting ---------------------------------------------------------

    def pending_count(self) -> int:
        return sum(len(state.pending) for state in self._symbols.values())

    def stats(self) -> dict[str, Any]:
        return {
            "symbols": sorted(self._symbols),
            "ticks": self.tick_count,
            "bars_closed": self.bars_closed,
            "bars_corrected": self.bars_corrected,
            "late_ticks": self.late_ticks,
            "dropped_late": self.dropped_late,
            "sequence_gaps": self.sequence_gaps,
            "no_trade_minutes": self.no_trade_minutes,
            "invalid_timestamps": self.invalid_timestamps,
            "pending_bars": self.pending_count(),
            "watermark": self._watermark.isoformat() if self._watermark else "",
        }


def aggregate_ticks(
    ticks: Iterable[MarketTick],
    *,
    lateness_seconds: float = DEFAULT_LATENESS_SECONDS,
) -> list[MarketBar]:
    """Replay a finite tick sequence into canonical 1m bars."""
    aggregator = OneMinuteBarAggregator(lateness_seconds=lateness_seconds)
    bars: list[MarketBar] = []
    for tick in ticks:
        bars.extend(aggregator.on_tick(tick))
    bars.extend(aggregator.close_all())
    return bars


@dataclass
class _BucketState:
    """One 5m bucket, held as the latest revision of each component minute."""

    symbol: str
    start: datetime
    components: dict[datetime, MarketBar] = field(default_factory=dict)
    revision: int = 0
    closed: bool = False

    def build(self, status: str) -> MarketBar:
        minutes = sorted(self.components)
        parts = [self.components[minute] for minute in minutes]
        return MarketBar(
            symbol=self.symbol,
            timeframe=TIMEFRAME_5M,
            start_at=self.start.isoformat(),
            end_at=(self.start + timedelta(minutes=FIVE_MINUTE_BUCKET)).isoformat(),
            open=parts[0].open,
            high=max(part.high for part in parts),
            low=min(part.low for part in parts),
            close=parts[-1].close,
            volume=sum(part.volume for part in parts),
            trade_count=sum(part.trade_count for part in parts),
            status=status,
            revision=self.revision,
            source=BAR_SOURCE_5M,
            sequence_gap=any(part.sequence_gap for part in parts),
        )


class FiveMinuteBarAggregator:
    """Aggregate canonical 1m bars into canonical 5m bars.

    5m is never taken from a provider; it is always rebuilt from the latest
    revision of each component minute:

        open   = earliest component's open
        high   = max component high
        low    = min component low
        close  = latest component's close
        volume = sum of component volume

    Three properties matter more than the arithmetic:

    - **A bucket is a time range, not a count of five bars.** P2 emits no bar
      for a minute nobody traded, so a bucket is built from whatever minutes
      it actually has. Nothing is padded, and no minute is borrowed from the
      next bucket to reach five.
    - **A corrected 1m replaces its minute, it never adds to it.** Receiving
      09:03 rev2 recomputes the whole bucket from the latest revision of each
      minute, so rev1's volume cannot be summed alongside rev2's.
    - **Emitted bars are frozen.** A correction produces a new revision; the
      5m a strategy already acted on stays exactly as it was.
    """

    def __init__(
        self,
        *,
        correction_window_minutes: int = DEFAULT_5M_CORRECTION_WINDOW_MINUTES,
    ) -> None:
        self._correction_window = timedelta(minutes=correction_window_minutes)
        self._buckets: dict[str, dict[datetime, _BucketState]] = {}
        self._watermark: datetime | None = None
        self.bars_in = 0
        self.bars_closed = 0
        self.bars_corrected = 0
        self.stale_revisions = 0
        self.dropped_late = 0
        self.ignored_timeframe = 0
        self.invalid_timestamps = 0

    def on_bar(self, bar: MarketBar) -> list[MarketBar]:
        """Apply one canonical 1m bar and return the 5m bars it finalized."""
        if bar.timeframe != TIMEFRAME_1M:
            # 5m must never be built from a provider bar or from itself.
            self.ignored_timeframe += 1
            return []
        minute = _parse_timestamp(bar.start_at)
        if minute is None:
            self.invalid_timestamps += 1
            return []

        self.bars_in += 1
        if self._watermark is None or minute > self._watermark:
            self._watermark = minute

        bucket_start = _floor_to_bucket(minute, FIVE_MINUTE_BUCKET)
        buckets = self._buckets.setdefault(bar.symbol, {})
        state = buckets.get(bucket_start)
        if state is None:
            if self._watermark - bucket_start > self._correction_window:
                self.dropped_late += 1
                return []
            state = _BucketState(symbol=bar.symbol, start=bucket_start)
            buckets[bucket_start] = state

        existing = state.components.get(minute)
        if existing is not None and bar.revision < existing.revision:
            self.stale_revisions += 1
            return self._finalize_due()

        # Replace, never accumulate: the latest revision is the whole truth
        # for that minute.
        state.components[minute] = bar

        if state.closed:
            state.revision += 1
            self.bars_corrected += 1
            return [state.build(STATUS_CORRECTED), *self._finalize_due()]
        return self._finalize_due()

    def flush(self, *, now: str | datetime | None = None) -> list[MarketBar]:
        """Finalize whatever the given clock makes due, without new 1m bars."""
        if now is not None:
            at = now if isinstance(now, datetime) else _parse_timestamp(now)
            if at is not None and (self._watermark is None or at > self._watermark):
                self._watermark = at
        return self._finalize_due()

    def close_all(self) -> list[MarketBar]:
        """Session end: close every bucket that still has components."""
        due = [
            (start, symbol, state)
            for symbol, buckets in self._buckets.items()
            for start, state in buckets.items()
            if not state.closed
        ]
        return self._emit(due)

    def _finalize_due(self) -> list[MarketBar]:
        if self._watermark is None:
            return []
        due = [
            (start, symbol, state)
            for symbol, buckets in self._buckets.items()
            for start, state in buckets.items()
            if not state.closed
            and start + timedelta(minutes=FIVE_MINUTE_BUCKET) <= self._watermark
        ]
        bars = self._emit(due)
        self._prune()
        return bars

    def _emit(self, due: list[tuple[datetime, str, _BucketState]]) -> list[MarketBar]:
        bars = []
        for _start, _symbol, state in sorted(due, key=lambda item: (item[0], item[1])):
            state.closed = True
            state.revision += 1
            self.bars_closed += 1
            bars.append(state.build(STATUS_CLOSED))
        return bars

    def _prune(self) -> None:
        if self._watermark is None:
            return
        cutoff = self._watermark - self._correction_window
        for buckets in self._buckets.values():
            for start in [s for s, state in buckets.items() if state.closed and s < cutoff]:
                del buckets[start]

    def stats(self) -> dict[str, Any]:
        return {
            "symbols": sorted(self._buckets),
            "bars_in": self.bars_in,
            "bars_closed": self.bars_closed,
            "bars_corrected": self.bars_corrected,
            "stale_revisions": self.stale_revisions,
            "dropped_late": self.dropped_late,
            "ignored_timeframe": self.ignored_timeframe,
            "invalid_timestamps": self.invalid_timestamps,
            "open_buckets": sum(
                1 for buckets in self._buckets.values() for s in buckets.values() if not s.closed
            ),
            "watermark": self._watermark.isoformat() if self._watermark else "",
        }


def aggregate_1m_to_5m(bars: Iterable[MarketBar]) -> list[MarketBar]:
    """Replay a finite 1m bar log into 5m bars, corrections included."""
    aggregator = FiveMinuteBarAggregator()
    emitted: list[MarketBar] = []
    for bar in bars:
        emitted.extend(aggregator.on_bar(bar))
    emitted.extend(aggregator.close_all())
    return emitted


def _floor_to_bucket(value: datetime, minutes: int) -> datetime:
    floored = value.replace(second=0, microsecond=0)
    return floored - timedelta(minutes=floored.minute % minutes)


def latest_bars(bars: Iterable[MarketBar]) -> list[MarketBar]:
    """Collapse an emission log to the latest revision of each bar.

    `on_tick` emits a bar again whenever a late tick corrects it, so the raw
    stream is an event log. Anything that sums bars must collapse it first or
    it counts corrected minutes twice.
    """
    latest: dict[tuple[str, str, str], MarketBar] = {}
    for bar in bars:
        key = (bar.symbol, bar.timeframe, bar.start_at)
        current = latest.get(key)
        if current is None or bar.revision >= current.revision:
            latest[key] = bar
    return sorted(latest.values(), key=lambda bar: (bar.start_at, bar.symbol, bar.timeframe))


def check_bar_volume(bars: Iterable[MarketBar], ticks: Sequence[MarketTick]) -> dict[str, Any]:
    """Cross-check aggregated bar volume against the source tick volume.

    A mismatch means ticks were dropped or double counted on the way in.
    """
    collapsed = latest_bars(bars)
    bar_volume = sum(bar.volume for bar in collapsed)
    tick_volume = sum(tick.trade_volume for tick in ticks)
    return {
        "bar_volume": bar_volume,
        "tick_volume": tick_volume,
        "bar_trade_count": sum(bar.trade_count for bar in collapsed),
        "tick_count": len(ticks),
        "consistent": bar_volume == tick_volume,
    }


def _parse_timestamp(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None

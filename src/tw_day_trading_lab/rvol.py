"""Relative volume: is the volume right now unusual for this time of day?

PA-P5 scope. Input is canonical `MarketBar` only, so the same code scores a
live session, a replay and a fixture.

Two ratios, both against the same time slot on previous days:

    TOD-RVOL = this bucket's volume        / median(same slot, past N days)
    Cum-RVOL = today's volume so far       / median(same slot cumulative, past N days)

The first says "is this bar unusually heavy". The second says "is today an
unusually busy day up to this point".

Missing bars are never counted as zero volume. PA-P2 emits no bar for a minute
nobody traded, so a day without a bar at 09:30 simply does not contribute a
09:30 sample - treating it as 0 would drag the baseline down and manufacture
fake volume spikes. A slot backed by too few days reports
`status="insufficient_data"` and no ratio at all, so a caller cannot trade on
a number derived from three days of history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence

from .bars import MarketBar, TIMEFRAME_5M

DEFAULT_LOOKBACK_DAYS = 20
STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"


@dataclass(frozen=True)
class SlotStat:
    """Baseline for one time-of-day slot, built from whole days that had it."""

    slot: str
    days: int
    median: float
    mean: float

    def to_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "days": self.days, "median": self.median, "mean": self.mean}


@dataclass(frozen=True)
class VolumeBaseline:
    symbol: str
    timeframe: str
    days: int
    slot_volume: Mapping[str, SlotStat] = field(default_factory=dict)
    slot_cumulative: Mapping[str, SlotStat] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "days": self.days,
            "slot_volume": {slot: stat.to_dict() for slot, stat in sorted(self.slot_volume.items())},
            "slot_cumulative": {
                slot: stat.to_dict() for slot, stat in sorted(self.slot_cumulative.items())
            },
        }


@dataclass(frozen=True)
class RvolResult:
    symbol: str
    timeframe: str
    start_at: str
    slot: str
    volume: int
    cumulative_volume: int
    tod_rvol: float | None
    cum_rvol: float | None
    tod_baseline: float | None
    cum_baseline: float | None
    sample_days: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_at": self.start_at,
            "slot": self.slot,
            "volume": self.volume,
            "cumulative_volume": self.cumulative_volume,
            "tod_rvol": self.tod_rvol,
            "cum_rvol": self.cum_rvol,
            "tod_baseline": self.tod_baseline,
            "cum_baseline": self.cum_baseline,
            "sample_days": self.sample_days,
            "status": self.status,
        }


def time_slot(start_at: str) -> str:
    """Return the comparable time-of-day key, e.g. '2026-08-17T09:30:00' -> '09:30'."""
    return start_at[11:16]


def trading_date_of(start_at: str) -> str:
    return start_at[:10]


def build_volume_baseline(
    bars: Iterable[MarketBar],
    *,
    symbol: str = "",
    timeframe: str = TIMEFRAME_5M,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> VolumeBaseline:
    """Build per-slot volume baselines from historical bars of one symbol.

    Only the most recent `lookback_days` distinct trading days are used, and a
    day contributes to a slot only if it actually has a bar there.
    """
    by_day: dict[str, dict[str, int]] = {}
    resolved_symbol = symbol
    for bar in bars:
        if bar.timeframe != timeframe:
            continue
        resolved_symbol = resolved_symbol or bar.symbol
        if symbol and bar.symbol != symbol:
            continue
        by_day.setdefault(trading_date_of(bar.start_at), {})[time_slot(bar.start_at)] = bar.volume

    days = sorted(by_day)[-lookback_days:] if lookback_days else sorted(by_day)

    slot_samples: dict[str, list[int]] = {}
    cumulative_samples: dict[str, list[int]] = {}
    for day in days:
        slots = by_day[day]
        running = 0
        for slot in sorted(slots):
            volume = slots[slot]
            running += volume
            slot_samples.setdefault(slot, []).append(volume)
            cumulative_samples.setdefault(slot, []).append(running)

    return VolumeBaseline(
        symbol=resolved_symbol,
        timeframe=timeframe,
        days=len(days),
        slot_volume=_slot_stats(slot_samples),
        slot_cumulative=_slot_stats(cumulative_samples),
    )


def compute_rvol_series(
    today_bars: Sequence[MarketBar],
    baseline: VolumeBaseline,
    *,
    min_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[RvolResult]:
    """Score today's bars against the baseline, in bucket order.

    Deterministic: the running cumulative is derived from the bars themselves,
    never from a clock or from call order.
    """
    results = []
    running = 0
    for bar in sorted(today_bars, key=lambda item: item.start_at):
        if bar.timeframe != baseline.timeframe:
            continue
        running += bar.volume
        slot = time_slot(bar.start_at)
        tod_stat = baseline.slot_volume.get(slot)
        cum_stat = baseline.slot_cumulative.get(slot)
        sample_days = min(
            tod_stat.days if tod_stat else 0,
            cum_stat.days if cum_stat else 0,
        )
        enough = sample_days >= min_days
        results.append(
            RvolResult(
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                start_at=bar.start_at,
                slot=slot,
                volume=bar.volume,
                cumulative_volume=running,
                # A ratio is withheld rather than shown small: a number here
                # would be traded on.
                tod_rvol=_ratio(bar.volume, tod_stat.median) if enough and tod_stat else None,
                cum_rvol=_ratio(running, cum_stat.median) if enough and cum_stat else None,
                tod_baseline=tod_stat.median if tod_stat else None,
                cum_baseline=cum_stat.median if cum_stat else None,
                sample_days=sample_days,
                status=STATUS_OK if enough else STATUS_INSUFFICIENT,
            )
        )
    return results


def _slot_stats(samples: Mapping[str, list[int]]) -> dict[str, SlotStat]:
    return {
        slot: SlotStat(
            slot=slot,
            days=len(values),
            # Median first: one exceptional day must not move the baseline.
            median=float(median(values)),
            mean=float(mean(values)),
        )
        for slot, values in samples.items()
        if values
    }


def _ratio(value: int, baseline: float) -> float | None:
    if not baseline:
        return None
    return value / baseline

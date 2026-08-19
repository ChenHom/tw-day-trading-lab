"""Historical intraday backfill: Shioaji kbars -> canonical MarketBar.

Composition layer between `market_data` (provider boundary) and `bars`
(provider-agnostic aggregation), which is why it lives in its own module:
neither of those may import the other.

Scope and boundary:

- Pre-market backfill of a known candidate list only. AGENTS.md forbids
  polling kbars during the session to scan the market; this is the other
  thing - a bounded historical fetch, behind an explicit gate that is off by
  default.
- Market data only. No order, no cancel, no `simulation=False`.
- Backfilled bars are tagged `source="shioaji_kbars"`, never
  `shioaji_tick_aggregated`, so a bar we aggregated ourselves is always
  distinguishable from one the provider gave us.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .bars import (
    STATUS_CLOSED,
    TIMEFRAME_1M,
    MarketBar,
    aggregate_1m_to_5m,
    append_bars,
    latest_bars,
)
from .market_data import SHARES_PER_LOT

BAR_SOURCE_KBARS = "shioaji_kbars"
# Continuous trading is 09:00-13:24 (265 one-minute bars) plus one closing
# auction bar, so a complete session backfill is 266 bars, not 270.
FULL_SESSION_MINUTES = 266

# Shioaji refuses a kbars request spanning more than 30 days, so a long history
# has to be fetched in pieces. Chunking here rather than at the CLI keeps it to
# one login: 2 years x 12 symbols is ~290 requests, and re-logging in for each
# one is both slow and a good way to hit the per-person connection cap.
MAX_KBARS_RANGE_DAYS = 30


def split_date_range(start_date: str, end_date: str, max_days: int = MAX_KBARS_RANGE_DAYS):
    """Split an inclusive date range into consecutive spans of at most max_days.

    Consecutive, not overlapping: a duplicated boundary day would be appended
    twice, and a skipped one would leave a hole no counter would notice.
    """
    if max_days < 1:
        raise ValueError("max_days must be >= 1")
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    if end < start:
        raise ValueError(f"end_date {end_date} precedes start_date {start_date}")
    spans = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=max_days - 1), end)
        spans.append((cursor.strftime("%Y-%m-%d"), stop.strftime("%Y-%m-%d")))
        cursor = stop + timedelta(days=1)
    return spans


def normalize_shioaji_kbars(
    symbol: str,
    kbars: Any,
    *,
    volume_in_lots: bool = True,
) -> list[MarketBar]:
    """Convert a columnar Shioaji `Kbars` object into canonical 1m bars.

    `Kbars` is column-oriented (`ts`, `Open`, `High`, `Low`, `Close`, `Volume`),
    with `ts` in nanoseconds and labelled by the minute the bar ENDS.

    `volume_in_lots` defaults to True. The SDK does not document the unit, but
    the official 2330 example is arithmetically decisive: Volume 2565 at a
    close near 2230 gives an Amount near 5.7 billion only if 2565 counts lots
    (2565 x 1000 x 2230). Read as shares it would be 5.7 million, off by
    1000x. `check_backfill_against_daily` still settles it empirically against
    a daily bar whose share unit is confirmed.
    """
    stamps = list(getattr(kbars, "ts", []) or [])
    opens = list(getattr(kbars, "Open", []) or [])
    highs = list(getattr(kbars, "High", []) or [])
    lows = list(getattr(kbars, "Low", []) or [])
    closes = list(getattr(kbars, "Close", []) or [])
    volumes = list(getattr(kbars, "Volume", []) or [])

    multiplier = SHARES_PER_LOT if volume_in_lots else 1
    bars = []
    for index, stamp in enumerate(stamps):
        marked = _from_nanoseconds(stamp)
        if marked is None:
            continue
        # Shioaji labels a kbar by the minute it ENDS: the bar stamped 09:01 is
        # the 09:00:00-09:00:59 minute, proven by its open matching the daily
        # open. Canonical bars are labelled by START, so shift back one minute.
        # The one exception is the closing-auction bar (stamped 13:30, really
        # covering 13:25-13:30); it lands at 13:29, which is the same 5m bucket.
        start = marked.replace(second=0, microsecond=0) - timedelta(minutes=1)
        try:
            bars.append(
                MarketBar(
                    symbol=symbol,
                    timeframe=TIMEFRAME_1M,
                    start_at=start.isoformat(),
                    end_at=(start + timedelta(minutes=1)).isoformat(),
                    open=float(opens[index]),
                    high=float(highs[index]),
                    low=float(lows[index]),
                    close=float(closes[index]),
                    volume=int(volumes[index]) * multiplier,
                    trade_count=0,
                    status=STATUS_CLOSED,
                    revision=1,
                    source=BAR_SOURCE_KBARS,
                )
            )
        except (IndexError, TypeError, ValueError):
            continue
    return bars


def check_backfill_against_daily(
    bars: Sequence[MarketBar],
    daily_volume_shares: int,
) -> dict[str, Any]:
    """Settle the kbar volume unit against a daily bar known to be in shares.

    FinMind `TaiwanStockPrice.Trading_Volume` is in shares (verified), so a
    day of backfilled 1m volume must sum to roughly the same number. A ratio
    near 1000 or 0.001 means the lot conversion is wrong in one direction.
    """
    total = sum(bar.volume for bar in bars)
    ratio = (total / daily_volume_shares) if daily_volume_shares else None
    return {
        "backfilled_volume": total,
        "daily_volume_shares": daily_volume_shares,
        "ratio": ratio,
        "bars": len(bars),
        # Intraday backfill excludes the closing auction, so exact equality is
        # not expected; an order-of-magnitude gap is what this catches.
        "unit_consistent": ratio is not None and 0.8 <= ratio <= 1.2,
    }


def run_gated_shioaji_kbars_backfill(
    *,
    api: Any,
    symbols: Sequence[str],
    start_date: str,
    end_date: str,
    enabled: bool,
    credentials_present: bool = True,
    store_dir: Path | None = None,
    volume_in_lots: bool = True,
    fetch: Callable[[Any, Any, str, str], Any] | None = None,
) -> dict[str, Any]:
    """Fetch historical 1m bars for a candidate list behind an explicit gate."""
    symbol_list = [str(symbol) for symbol in symbols]
    report: dict[str, Any] = {
        "status": "blocked",
        "mode": "shioaji_kbars_backfill",
        "start_date": start_date,
        "end_date": end_date,
        "checks": {
            "gate_enabled": enabled,
            "credentials_present": credentials_present,
            "simulation_api": getattr(api, "simulation", None) is True,
            "candidate_scoped": True,
            "symbol_count": len(symbol_list),
            "orders_allowed": False,
            "intraday_polling": False,
        },
        "side_effects": [],
        "review_reason": "",
        "symbols": [],
    }
    if not enabled:
        report["review_reason"] = "enable_kbars_backfill_required"
        return report
    if not credentials_present:
        report["review_reason"] = "shioaji_credentials_required"
        return report
    if not symbol_list:
        report["review_reason"] = "candidate_symbols_required"
        return report
    if getattr(api, "simulation", None) is not True:
        report["review_reason"] = "simulation_api_required"
        return report

    try:
        spans = split_date_range(start_date, end_date)
    except ValueError as error:
        report["review_reason"] = "invalid_date_range"
        report["error"] = str(error)
        return report

    report["side_effects"] = ["kbars_fetch"]
    report["checks"]["request_spans"] = len(spans)
    fetch_kbars = fetch or _default_fetch
    stored: list[str] = []
    for symbol in symbol_list:
        entry: dict[str, Any] = {"symbol": symbol}
        try:
            contract = api.Contracts.Stocks[symbol]
            fetched = []
            for span_start, span_end in spans:
                raw = fetch_kbars(api, contract, span_start, span_end)
                fetched.extend(normalize_shioaji_kbars(symbol, raw, volume_in_lots=volume_in_lots))
        except Exception as error:
            entry.update({"status": "failed", "error": str(error), "bars_1m": 0})
            report["symbols"].append(entry)
            continue

        bars_1m = latest_bars(fetched)
        bars_5m = latest_bars(aggregate_1m_to_5m(bars_1m))
        entry.update(
            {
                "status": "ok",
                "bars_1m": len(bars_1m),
                "bars_5m": len(bars_5m),
                "days": sorted({bar.start_at[:10] for bar in bars_1m}),
                "session_complete": _session_completeness(bars_1m),
            }
        )
        if store_dir is not None and bars_1m:
            stored.extend(str(path) for path in append_bars(Path(store_dir), bars_1m + bars_5m))
        report["symbols"].append(entry)

    report["stored"] = stored
    failed = [item for item in report["symbols"] if item.get("status") == "failed"]
    report["status"] = "partial" if failed and len(failed) < len(symbol_list) else (
        "failed" if failed else "ok"
    )
    if failed:
        report["review_reason"] = "kbars_fetch_failed"
    return report


def _default_fetch(api: Any, contract: Any, start_date: str, end_date: str) -> Any:
    return api.kbars(contract, start=start_date, end=end_date)


def _session_completeness(bars: Sequence[MarketBar]) -> dict[str, Any]:
    """Per-day bar counts, so a truncated fetch is visible instead of assumed."""
    by_day: dict[str, int] = {}
    for bar in bars:
        by_day[bar.start_at[:10]] = by_day.get(bar.start_at[:10], 0) + 1
    return {
        "expected_minutes": FULL_SESSION_MINUTES,
        "by_day": dict(sorted(by_day.items())),
        "short_days": sorted(day for day, count in by_day.items() if count < FULL_SESSION_MINUTES),
    }


def _from_nanoseconds(value: Any) -> datetime | None:
    """Decode a Shioaji kbar `ts` into exchange local wall-clock time.

    `ts` encodes Taiwan local time as if it were a UTC epoch: the official
    example `1779094860000000000` is 2026-05-18 09:01 local, which is what
    UTC decoding yields. Decoding it with the machine's local timezone would
    shift every bar by the UTC offset (09:01 becomes 17:01 on a UTC+8 host)
    and would give different answers on differently configured machines.
    """
    try:
        moment = datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return moment.replace(tzinfo=None)

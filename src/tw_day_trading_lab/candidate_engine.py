from __future__ import annotations

import math

from .models import CandidateInput, CandidateScore


MIN_TRADING_MONEY = 80_000_000
MIN_INTRADAY_RANGE_PCT = 1.5
MAX_CROWDING_RISK = 0.8


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def infer_archetype(candidate: CandidateInput) -> str:
    if candidate.change_pct >= 4 and candidate.volume_expansion >= 2:
        return "breakout_continuation"
    if candidate.structure_quality >= 0.65 and candidate.change_pct < 4:
        return "expansion_from_base"
    return "theme_follower"


def score_candidate(candidate: CandidateInput, rank: int = 0) -> CandidateScore:
    liquidity_score = clamp((math.log10(max(candidate.trading_money, 1)) - 7.0) / 3.0)
    event_score = clamp((candidate.volume_expansion / 3.0) * 0.55 + candidate.theme_strength * 0.45)
    structure_score = clamp(candidate.structure_quality)
    continuity_score = clamp((candidate.change_pct / 6.0) * 0.45 + (candidate.intraday_range_pct / 5.0) * 0.55)
    crowding_penalty = clamp(candidate.crowding_risk)

    total = (
        liquidity_score * 24
        + event_score * 22
        + structure_score * 24
        + continuity_score * 22
        - crowding_penalty * 12
    )
    total_score = round(clamp(total / 92.0) * 100, 2)

    reasons: list[str] = []
    downgrade_reasons: list[str] = []

    if candidate.trading_money >= MIN_TRADING_MONEY:
        reasons.append("liquid_enough")
    else:
        downgrade_reasons.append("low_trading_money")

    if candidate.intraday_range_pct >= MIN_INTRADAY_RANGE_PCT:
        reasons.append("enough_intraday_range")
    else:
        downgrade_reasons.append("range_too_small")

    if candidate.structure_quality >= 0.55:
        reasons.append("usable_structure")
    else:
        downgrade_reasons.append("weak_structure")

    if candidate.crowding_risk <= MAX_CROWDING_RISK:
        reasons.append("crowding_acceptable")
    else:
        downgrade_reasons.append("crowding_too_high")

    if candidate.data_quality != "ok":
        downgrade_reasons.append(f"data_quality_{candidate.data_quality}")

    next_day_actionable = (
        total_score >= 55
        and candidate.trading_money >= MIN_TRADING_MONEY
        and candidate.intraday_range_pct >= MIN_INTRADAY_RANGE_PCT
        and candidate.structure_quality >= 0.45
        and candidate.crowding_risk <= MAX_CROWDING_RISK
        and candidate.data_quality == "ok"
    )

    return CandidateScore(
        symbol=candidate.symbol,
        name=candidate.name,
        rank=rank,
        archetype=infer_archetype(candidate),
        total_score=total_score,
        liquidity_score=round(liquidity_score * 100, 2),
        event_score=round(event_score * 100, 2),
        structure_score=round(structure_score * 100, 2),
        continuity_score=round(continuity_score * 100, 2),
        crowding_penalty=round(crowding_penalty * 100, 2),
        next_day_actionable=next_day_actionable,
        reasons=tuple(reasons),
        downgrade_reasons=tuple(downgrade_reasons),
    )


def rank_candidates(candidates: list[CandidateInput], limit: int = 80) -> list[CandidateScore]:
    scored = [score_candidate(candidate) for candidate in candidates]
    scored.sort(key=lambda item: (item.next_day_actionable, item.total_score), reverse=True)
    return [
        CandidateScore(**{**score.to_dict(), "rank": index})
        for index, score in enumerate(scored[:limit], start=1)
    ]


from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CandidateInput:
    symbol: str
    name: str
    trading_money: float
    change_pct: float
    intraday_range_pct: float
    volume_expansion: float
    theme_strength: float
    structure_quality: float
    crowding_risk: float
    data_quality: str = "ok"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateInput":
        return cls(
            symbol=str(data["symbol"]),
            name=str(data.get("name", data["symbol"])),
            trading_money=float(data.get("trading_money", 0)),
            change_pct=float(data.get("change_pct", 0)),
            intraday_range_pct=float(data.get("intraday_range_pct", 0)),
            volume_expansion=float(data.get("volume_expansion", 0)),
            theme_strength=float(data.get("theme_strength", 0)),
            structure_quality=float(data.get("structure_quality", 0)),
            crowding_risk=float(data.get("crowding_risk", 0)),
            data_quality=str(data.get("data_quality", "ok")),
        )


@dataclass(frozen=True)
class CandidateScore:
    symbol: str
    name: str
    rank: int
    archetype: str
    total_score: float
    liquidity_score: float
    event_score: float
    structure_score: float
    continuity_score: float
    crowding_penalty: float
    next_day_actionable: bool
    reasons: tuple[str, ...]
    downgrade_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        data["downgrade_reasons"] = list(self.downgrade_reasons)
        return data


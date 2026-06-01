from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .ledger import DuplicateIntentError, OrderIntent, PaperLedger


@dataclass(frozen=True)
class SignalIntent:
    trading_date: str
    strategy_id: str
    symbol: str
    setup_id: str
    side: str
    quantity: int
    price: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SignalIntent":
        return cls(
            trading_date=str(data["trading_date"]),
            strategy_id=str(data["strategy_id"]),
            symbol=str(data["symbol"]),
            setup_id=str(data["setup_id"]),
            side=str(data["side"]),
            quantity=int(data["quantity"]),
            price=float(data["price"]) if data.get("price") is not None else None,
        )

    def to_order_intent(self) -> OrderIntent:
        return OrderIntent(
            trading_date=self.trading_date,
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            setup_id=self.setup_id,
            side=self.side,
        )


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    quantity: int | None = None
    price: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RiskDecision":
        return cls(
            approved=bool(data["approved"]),
            reason=str(data.get("reason", "")),
            quantity=int(data["quantity"]) if data.get("quantity") is not None else None,
            price=float(data["price"]) if data.get("price") is not None else None,
        )


@dataclass(frozen=True)
class BrokerTrade:
    idempotency_key: str
    broker_order_id: str
    trading_date: str
    symbol: str
    side: str
    quantity: int
    price: float | None
    status: str
    raw_status: str
    source: str = "simulation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LedgerPosition:
    position_id: str
    idempotency_key: str
    trading_date: str
    symbol: str
    side: str
    quantity: int
    average_price: float | None
    status: str
    source: str = "simulation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimulationResult:
    status: str
    sample_type: str
    expectancy_eligible: bool
    signal: SignalIntent
    risk_decision: RiskDecision
    order_intent: OrderIntent | None = None
    trade: BrokerTrade | None = None
    position: LedgerPosition | None = None
    review_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sample_type": self.sample_type,
            "expectancy_eligible": self.expectancy_eligible,
            "signal": asdict(self.signal),
            "risk_decision": asdict(self.risk_decision),
            "order_intent": asdict(self.order_intent) if self.order_intent else None,
            "broker_trade": self.trade.to_dict() if self.trade else None,
            "ledger_position": self.position.to_dict() if self.position else None,
            "review_reason": self.review_reason,
        }


class SimulationBroker(Protocol):
    def login(self) -> dict[str, str]:
        """Return a simulation session object owned by the broker adapter."""

    def place_order(
        self,
        session: dict[str, str],
        intent: OrderIntent,
        signal: SignalIntent,
        decision: RiskDecision,
    ) -> BrokerTrade:
        """Submit a simulation order and return a normalized broker trade."""


class DryRunSimulationBroker:
    def __init__(self, raw_status: str = "Filled") -> None:
        self.raw_status = raw_status
        self.login_count = 0
        self.place_order_count = 0

    def login(self) -> dict[str, str]:
        self.login_count += 1
        return {"mode": "simulation", "session_id": f"dry-run-{self.login_count}"}

    def place_order(
        self,
        session: dict[str, str],
        intent: OrderIntent,
        signal: SignalIntent,
        decision: RiskDecision,
    ) -> BrokerTrade:
        self.place_order_count += 1
        quantity = decision.quantity if decision.quantity is not None else signal.quantity
        price = decision.price if decision.price is not None else signal.price
        return BrokerTrade(
            idempotency_key=intent.idempotency_key,
            broker_order_id=f"{session['session_id']}-order-{self.place_order_count}",
            trading_date=intent.trading_date,
            symbol=intent.symbol,
            side=intent.side,
            quantity=quantity,
            price=price,
            status=normalize_broker_status(self.raw_status),
            raw_status=self.raw_status,
        )


def normalize_broker_status(raw_status: str) -> str:
    normalized = raw_status.strip().lower().replace("_", "").replace(" ", "")
    status_map = {
        "filled": "filled",
        "partfilled": "partial_filled",
        "partialfilled": "partial_filled",
        "submitted": "submitted",
        "pending": "submitted",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "rejected": "rejected",
        "failed": "rejected",
    }
    return status_map.get(normalized, "needs_review")


class ShioajiSimulationAdapter:
    def __init__(self, broker: SimulationBroker, ledger: PaperLedger) -> None:
        self._broker = broker
        self._ledger = ledger
        self._session: dict[str, str] | None = None

    def execute(self, signal: SignalIntent, decision: RiskDecision) -> SimulationResult:
        if not decision.approved:
            return SimulationResult(
                status="risk_rejected",
                sample_type="simulation",
                expectancy_eligible=False,
                signal=signal,
                risk_decision=decision,
                review_reason=decision.reason,
            )

        intent = signal.to_order_intent()
        try:
            self._ledger.register_intent(intent)
        except DuplicateIntentError as error:
            return SimulationResult(
                status="duplicate",
                sample_type="simulation",
                expectancy_eligible=False,
                signal=signal,
                risk_decision=decision,
                order_intent=intent,
                review_reason=str(error),
            )

        session = self._ensure_session()
        trade = self._broker.place_order(session, intent, signal, decision)
        trade = replace(trade, status=normalize_broker_status(trade.raw_status or trade.status))
        status, review_reason = self._status_from_trade(intent, trade)
        position = self._position_from_trade(trade) if status == "simulated" else None
        if status != "simulated":
            self._ledger.close_intent(intent)

        return SimulationResult(
            status=status,
            sample_type="simulation",
            expectancy_eligible=False,
            signal=signal,
            risk_decision=decision,
            order_intent=intent,
            trade=trade,
            position=position,
            review_reason=review_reason,
        )

    def _ensure_session(self) -> dict[str, str]:
        if self._session is None:
            self._session = self._broker.login()
        return self._session

    def _status_from_trade(self, intent: OrderIntent, trade: BrokerTrade) -> tuple[str, str]:
        if trade.idempotency_key != intent.idempotency_key:
            return "needs_review", "broker_idempotency_mismatch"
        if trade.status == "needs_review":
            return "needs_review", "broker_status_needs_review"
        if trade.status in {"cancelled", "rejected"}:
            return "broker_rejected", trade.status
        return "simulated", ""

    def _position_from_trade(self, trade: BrokerTrade) -> LedgerPosition:
        return LedgerPosition(
            position_id=f"sim:{trade.broker_order_id}",
            idempotency_key=trade.idempotency_key,
            trading_date=trade.trading_date,
            symbol=trade.symbol,
            side=trade.side,
            quantity=trade.quantity,
            average_price=trade.price,
            status="open",
        )


def reconcile_broker_trades(trades: list[BrokerTrade], ledger: PaperLedger) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    needs_review = 0
    matched = 0

    for trade in trades:
        normalized_status = normalize_broker_status(trade.raw_status or trade.status)
        review_reasons: list[str] = []
        if normalized_status == "needs_review":
            review_reasons.append("broker_status_needs_review")
        if not ledger.has_open_key(trade.idempotency_key):
            review_reasons.append("ledger_missing_open_intent")

        validity = "needs_review" if review_reasons else "valid"
        if review_reasons:
            needs_review += 1
        else:
            matched += 1

        samples.append(
            {
                "sample_type": "simulation",
                "validity": validity,
                "expectancy_eligible": False,
                "idempotency_key": trade.idempotency_key,
                "broker_order_id": trade.broker_order_id,
                "symbol": trade.symbol,
                "status": normalized_status,
                "review_reason": ",".join(review_reasons),
            }
        )

    return {
        "checked": len(trades),
        "matched": matched,
        "needs_review": needs_review,
        "samples": samples,
    }


class FileExecutionSyncStore:
    """Persist simulation execution state for restart reconciliation."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"broker_trades": [], "open_positions": [], "results": []}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("execution sync store must be a JSON object")
        return {
            "broker_trades": list(raw.get("broker_trades", [])),
            "open_positions": list(raw.get("open_positions", [])),
            "results": list(raw.get("results", [])),
        }

    def record_result(self, result: SimulationResult) -> None:
        snapshot = self.load_snapshot()
        result_payload = result.to_dict()
        snapshot["results"].append(result_payload)
        if result.trade:
            snapshot["broker_trades"].append(result.trade.to_dict())
        if result.position:
            snapshot["open_positions"] = _upsert_position_payload(
                snapshot["open_positions"],
                result.position.to_dict(),
            )
        self._write_snapshot(snapshot)

    def _write_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def restore_ledger_from_positions(positions: list[LedgerPosition]) -> PaperLedger:
    """Rebuild a paper ledger's open intent keys from persisted open positions."""
    ledger = PaperLedger()
    for position in positions:
        if position.status != "open":
            continue
        ledger.register_intent(order_intent_from_idempotency_key(position.idempotency_key))
    return ledger


def build_restart_sync_report(
    broker_trades: list[BrokerTrade],
    open_positions: list[LedgerPosition],
    ledger: PaperLedger,
) -> dict[str, Any]:
    """Compare broker trades and persisted ledger positions after a restart."""
    report = reconcile_broker_trades(broker_trades, ledger)
    broker_keys = {trade.idempotency_key for trade in broker_trades}
    samples = list(report["samples"])
    needs_review = int(report["needs_review"])
    matched = int(report["matched"])
    checked = int(report["checked"])

    for position in open_positions:
        if position.status != "open" or position.idempotency_key in broker_keys:
            continue
        checked += 1
        needs_review += 1
        matched = max(0, matched)
        samples.append(
            {
                "sample_type": "simulation",
                "validity": "needs_review",
                "expectancy_eligible": False,
                "idempotency_key": position.idempotency_key,
                "broker_order_id": "",
                "symbol": position.symbol,
                "status": "ledger_open_without_broker_trade",
                "review_reason": "ledger_missing_broker_trade",
            }
        )

    return {
        "checked": checked,
        "matched": matched,
        "needs_review": needs_review,
        "samples": samples,
    }


def broker_trades_from_payload(rows: list[dict[str, Any]]) -> list[BrokerTrade]:
    return [BrokerTrade(**row) for row in rows]


def ledger_positions_from_payload(rows: list[dict[str, Any]]) -> list[LedgerPosition]:
    return [LedgerPosition(**row) for row in rows]


def order_intent_from_idempotency_key(idempotency_key: str) -> OrderIntent:
    parts = idempotency_key.split(":")
    if len(parts) != 5:
        raise ValueError(f"invalid idempotency key: {idempotency_key}")
    trading_date, strategy_id, symbol, setup_id, side = parts
    return OrderIntent(
        trading_date=trading_date,
        strategy_id=strategy_id,
        symbol=symbol,
        setup_id=setup_id,
        side=side,
    )


def _upsert_position_payload(
    rows: list[dict[str, Any]],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        *[item for item in rows if item.get("idempotency_key") != row.get("idempotency_key")],
        row,
    ]


def render_simulation_markdown(trading_date: str, results: list[SimulationResult]) -> str:
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    expectancy_eligible = sum(1 for result in results if result.expectancy_eligible)

    lines = [
        f"# Simulation Report - {trading_date}",
        "",
        "## Simulation samples",
        "",
        f"- total: {len(results)}",
        f"- expectancy eligible: {expectancy_eligible}",
        "- replay expectancy: simulation samples are reported separately and never included",
        "",
        "## Status Summary",
        "",
    ]
    for status in sorted(status_counts):
        lines.append(f"- {status}: {status_counts[status]}")
    if not status_counts:
        lines.append("- none: 0")

    lines.extend(["", "## Orders", ""])
    for result in results:
        key = result.order_intent.idempotency_key if result.order_intent else "-"
        lines.append(
            f"- {result.signal.symbol} {result.signal.side} {result.signal.quantity} "
            f"status={result.status} key={key}"
        )
    return "\n".join(lines) + "\n"

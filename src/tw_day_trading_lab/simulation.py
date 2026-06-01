from __future__ import annotations

import base64
import hashlib
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
class ShioajiOrderRequest:
    trading_date: str
    symbol: str
    side: str
    quantity: int
    price: float | None
    idempotency_key: str
    custom_field: str
    price_type: str = "LMT"
    order_type: str = "ROD"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCallbackEvent:
    stat: str
    broker_order_id: str
    idempotency_key: str
    trading_date: str
    symbol: str
    side: str
    quantity: int
    price: float | None
    normalized_status: str
    raw_status: str
    review_reason: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_broker_trade(self) -> BrokerTrade | None:
        if not self.idempotency_key or self.review_reason:
            return None
        return BrokerTrade(
            idempotency_key=self.idempotency_key,
            broker_order_id=self.broker_order_id,
            trading_date=self.trading_date,
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            price=self.price,
            status=self.normalized_status,
            raw_status=self.raw_status,
        )


@dataclass(frozen=True)
class ExecutionLifecycleDecision:
    idempotency_key: str
    broker_order_id: str
    normalized_status: str
    ledger_effect: str
    action: str
    needs_review: bool
    review_reason: str = ""

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


class ShioajiOrderGateway(Protocol):
    def login(self) -> dict[str, str]:
        """Return a gateway-owned Shioaji simulation session."""

    def place_order(
        self,
        session: dict[str, str],
        request: ShioajiOrderRequest,
    ) -> dict[str, Any]:
        """Submit a Shioaji-shaped request and return the raw order response."""


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


class ShioajiOrderRequestBroker:
    """Adapt a Shioaji-shaped gateway to the internal simulation broker protocol."""

    def __init__(self, gateway: ShioajiOrderGateway) -> None:
        self._gateway = gateway

    def login(self) -> dict[str, str]:
        return self._gateway.login()

    def place_order(
        self,
        session: dict[str, str],
        intent: OrderIntent,
        signal: SignalIntent,
        decision: RiskDecision,
    ) -> BrokerTrade:
        request = build_shioaji_order_request(intent, signal, decision)
        response = self._gateway.place_order(session, request)
        raw_status = str(_first_non_empty(response.get("raw_status"), response.get("status")) or "")
        return BrokerTrade(
            idempotency_key=str(
                _first_non_empty(response.get("idempotency_key"), request.idempotency_key) or ""
            ),
            broker_order_id=str(
                _first_non_empty(
                    response.get("broker_order_id"),
                    response.get("order_id"),
                    response.get("id"),
                )
                or ""
            ),
            trading_date=request.trading_date,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            status=normalize_broker_status(raw_status),
            raw_status=raw_status,
        )


class ShioajiSdkSimulationGateway:
    """Small Shioaji SDK boundary that can be tested with a fake API object."""

    def __init__(
        self,
        api: Any,
        api_key: str,
        secret_key: str,
        *,
        account: Any = None,
    ) -> None:
        if getattr(api, "simulation", True) is not True:
            raise ValueError("ShioajiSdkSimulationGateway requires api.simulation=True")
        self._api = api
        self._api_key = api_key
        self._secret_key = secret_key
        self._account = account

    def login(self) -> dict[str, str]:
        accounts = self._api.login(
            api_key=self._api_key,
            secret_key=self._secret_key,
            fetch_contract=True,
            subscribe_trade=True,
        )
        if self._account is None:
            self._account = getattr(self._api, "stock_account", None)
            if self._account is None and accounts:
                self._account = accounts[0]
        return {"mode": "simulation", "session_id": "shioaji-sdk"}

    def place_order(
        self,
        session: dict[str, str],
        request: ShioajiOrderRequest,
    ) -> dict[str, Any]:
        contract = self._api.Contracts.Stocks[request.symbol]
        order = self._api.Order(
            price=request.price if request.price is not None else 0,
            quantity=request.quantity,
            action=self._resolve_sdk_value("Action", request.side.capitalize()),
            price_type=self._resolve_sdk_value("StockPriceType", request.price_type),
            order_type=self._resolve_sdk_value("OrderType", request.order_type),
            account=self._account,
            custom_field=request.custom_field,
        )
        raw_trade = self._api.place_order(contract, order)
        return _normalize_shioaji_place_order_response(raw_trade, request)

    def _resolve_sdk_value(self, enum_name: str, member_name: str) -> Any:
        try:
            import shioaji as sj  # type: ignore

            enum_cls = getattr(sj.constant, enum_name)
            return getattr(enum_cls, member_name)
        except Exception:
            return member_name


class ShioajiCallbackStream:
    """Register a Shioaji order callback and persist normalized simulation events."""

    def __init__(
        self,
        api: Any,
        store: "FileExecutionSyncStore",
        trading_date: str,
    ) -> None:
        if getattr(api, "simulation", True) is not True:
            raise ValueError("ShioajiCallbackStream requires api.simulation=True")
        self._api = api
        self._store = store
        self._trading_date = trading_date
        self.callback_count = 0

    def start(self) -> None:
        self._api.set_order_callback(self.handle_callback)

    def handle_callback(self, stat: Any, msg: Any) -> ExecutionCallbackEvent:
        snapshot = self._store.load_snapshot()
        event = normalize_shioaji_order_callback(
            stat,
            msg,
            trading_date=self._trading_date,
            custom_field_map=snapshot["custom_field_map"],
        )
        self._store.record_callback_event(event)
        self.callback_count += 1
        return event


def build_shioaji_order_request(
    intent: OrderIntent,
    signal: SignalIntent,
    decision: RiskDecision,
) -> ShioajiOrderRequest:
    """Build the Shioaji order request contract without importing the SDK."""
    quantity = decision.quantity if decision.quantity is not None else signal.quantity
    price = decision.price if decision.price is not None else signal.price
    return ShioajiOrderRequest(
        trading_date=intent.trading_date,
        symbol=intent.symbol,
        side=intent.side,
        quantity=quantity,
        price=price,
        idempotency_key=intent.idempotency_key,
        custom_field=build_shioaji_custom_field(intent.idempotency_key),
        price_type="LMT" if price is not None else "MKT",
        order_type="ROD",
    )


def build_shioaji_custom_field(idempotency_key: str) -> str:
    """Build a stable 6-char Shioaji custom_field token for an idempotency key."""
    digest = hashlib.blake2s(idempotency_key.encode("utf-8"), digest_size=5).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")[:6]


def _normalize_shioaji_place_order_response(
    raw_trade: Any,
    request: ShioajiOrderRequest,
) -> dict[str, Any]:
    payload = _to_plain_mapping(raw_trade)
    order = _to_plain_mapping(payload.get("order", {}))
    status = _to_plain_mapping(payload.get("status", {}))
    return {
        "broker_order_id": str(
            _first_non_empty(
                order.get("id"),
                order.get("ordno"),
                payload.get("id"),
                payload.get("order_id"),
            )
            or ""
        ),
        "custom_field": str(
            _first_non_empty(
                order.get("custom_field"),
                order.get("customField"),
                payload.get("custom_field"),
                request.custom_field,
            )
            or ""
        ),
        "status": str(
            _first_non_empty(
                status.get("status"),
                payload.get("status"),
                "submitted",
            )
            or ""
        ),
        "raw_status": str(
            _first_non_empty(
                status.get("status"),
                payload.get("raw_status"),
                payload.get("status"),
                "submitted",
            )
            or ""
        ),
        "raw": payload,
    }


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


def classify_execution_lifecycle(event: ExecutionCallbackEvent) -> ExecutionLifecycleDecision:
    """Classify how a callback should affect ledger lifecycle state."""
    base = {
        "idempotency_key": event.idempotency_key,
        "broker_order_id": event.broker_order_id,
        "normalized_status": event.normalized_status,
    }
    if event.review_reason or event.normalized_status == "needs_review":
        return ExecutionLifecycleDecision(
            **base,
            ledger_effect="hold_for_review",
            action="callback_needs_review",
            needs_review=True,
            review_reason=event.review_reason or "callback_status_needs_review",
        )
    if event.normalized_status == "filled":
        return ExecutionLifecycleDecision(
            **base,
            ledger_effect="open_position",
            action="confirm_open_position",
            needs_review=False,
        )
    if event.normalized_status == "partial_filled":
        return ExecutionLifecycleDecision(
            **base,
            ledger_effect="hold_for_review",
            action="partial_fill_manual_reconciliation",
            needs_review=True,
            review_reason="partial_fill_requires_policy",
        )
    if event.normalized_status in {"cancelled", "rejected"}:
        return ExecutionLifecycleDecision(
            **base,
            ledger_effect="close_intent",
            action=f"{event.normalized_status}_release_intent",
            needs_review=False,
        )
    if event.normalized_status == "submitted":
        return ExecutionLifecycleDecision(
            **base,
            ledger_effect="none",
            action="keep_pending_order",
            needs_review=False,
        )
    return ExecutionLifecycleDecision(
        **base,
        ledger_effect="hold_for_review",
        action="unknown_status_manual_reconciliation",
        needs_review=True,
        review_reason="unknown_lifecycle_status",
    )


def build_callback_event_key(event: ExecutionCallbackEvent) -> str:
    """Build a stable key for idempotent callback persistence."""
    return "|".join(
        [
            event.trading_date,
            event.broker_order_id,
            event.idempotency_key,
            event.normalized_status,
            str(event.quantity),
            "" if event.price is None else str(event.price),
        ]
    )


def build_callback_order_key(event: ExecutionCallbackEvent) -> str:
    """Build the key used to track status progression for one broker order."""
    return "|".join([event.trading_date, event.broker_order_id, event.idempotency_key])


def callback_status_precedence(normalized_status: str) -> int:
    """Return monotonic precedence for callback status ordering."""
    return {
        "submitted": 10,
        "partial_filled": 20,
        "filled": 30,
        "cancelled": 40,
        "rejected": 40,
        "needs_review": 100,
    }.get(normalized_status, 100)


class FileExecutionSyncStore:
    """Persist simulation execution state for restart reconciliation."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_execution_sync_snapshot()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("execution sync store must be a JSON object")
        return {
            "broker_trades": list(raw.get("broker_trades", [])),
            "open_positions": list(raw.get("open_positions", [])),
            "results": list(raw.get("results", [])),
            "callback_events": list(raw.get("callback_events", [])),
            "lifecycle_decisions": list(raw.get("lifecycle_decisions", [])),
            "callback_event_keys": list(raw.get("callback_event_keys", [])),
            "callback_status_by_order": dict(raw.get("callback_status_by_order", {})),
            "callback_ordering_issues": list(raw.get("callback_ordering_issues", [])),
            "custom_field_map": dict(raw.get("custom_field_map", {})),
        }

    def record_result(self, result: SimulationResult) -> None:
        snapshot = self.load_snapshot()
        result_payload = result.to_dict()
        snapshot["results"].append(result_payload)
        if result.order_intent:
            snapshot["custom_field_map"][
                build_shioaji_custom_field(result.order_intent.idempotency_key)
            ] = result.order_intent.idempotency_key
        if result.trade:
            snapshot["broker_trades"].append(result.trade.to_dict())
        if result.position:
            snapshot["open_positions"] = _upsert_position_payload(
                snapshot["open_positions"],
                result.position.to_dict(),
            )
        self._write_snapshot(snapshot)

    def record_callback_event(self, event: ExecutionCallbackEvent) -> bool:
        snapshot = self.load_snapshot()
        event_key = build_callback_event_key(event)
        if event_key in set(snapshot["callback_event_keys"]):
            return False
        order_key = build_callback_order_key(event)
        current_status = snapshot["callback_status_by_order"].get(order_key)
        if current_status and callback_status_precedence(
            event.normalized_status
        ) < callback_status_precedence(current_status):
            snapshot["callback_ordering_issues"].append(
                {
                    "reason": "stale_callback_status",
                    "order_key": order_key,
                    "event_key": event_key,
                    "incoming_status": event.normalized_status,
                    "current_status": current_status,
                }
            )
            self._write_snapshot(snapshot)
            return False
        snapshot["callback_event_keys"].append(event_key)
        snapshot["callback_status_by_order"][order_key] = event.normalized_status
        snapshot["callback_events"].append(event.to_dict())
        snapshot["lifecycle_decisions"].append(classify_execution_lifecycle(event).to_dict())
        trade = event.to_broker_trade()
        if trade:
            snapshot["broker_trades"].append(trade.to_dict())
        self._write_snapshot(snapshot)
        return True

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


def normalize_shioaji_order_callback(
    stat: Any,
    msg: Any,
    *,
    trading_date: str,
    custom_field_map: dict[str, str] | None = None,
) -> ExecutionCallbackEvent:
    """Normalize a Shioaji order callback payload into the lab execution contract."""
    payload = _to_plain_mapping(msg)
    order = _to_plain_mapping(payload.get("order", {}))
    contract = _to_plain_mapping(payload.get("contract", {}))
    status = _to_plain_mapping(payload.get("status", {}))

    raw_status = str(
        _first_non_empty(
            status.get("status"),
            status.get("order_status"),
            payload.get("status"),
            stat,
        )
        or ""
    )
    custom_field = str(
        _first_non_empty(order.get("custom_field"), order.get("customField"))
        or ""
    )
    explicit_idempotency_key = str(
        _first_non_empty(order.get("idempotency_key"), payload.get("idempotency_key")) or ""
    )
    idempotency_key = _resolve_callback_idempotency_key(
        explicit_idempotency_key,
        custom_field,
        custom_field_map or {},
    )
    broker_order_id = str(
        _first_non_empty(
            order.get("id"),
            order.get("order_id"),
            order.get("ordno"),
            payload.get("order_id"),
        )
        or ""
    )
    symbol = str(
        _first_non_empty(
            contract.get("code"),
            contract.get("symbol"),
            order.get("code"),
            payload.get("symbol"),
        )
        or ""
    )
    normalized_status = normalize_broker_status(raw_status)
    review_reasons: list[str] = []
    if not idempotency_key and custom_field:
        review_reasons.append("unresolved_custom_field")
    elif not idempotency_key:
        review_reasons.append("missing_idempotency_key")
    if not broker_order_id:
        review_reasons.append("missing_broker_order_id")
    if not symbol:
        review_reasons.append("missing_symbol")
    if normalized_status == "needs_review":
        review_reasons.append("broker_status_needs_review")
    if review_reasons:
        normalized_status = "needs_review"

    return ExecutionCallbackEvent(
        stat=str(stat),
        broker_order_id=broker_order_id,
        idempotency_key=idempotency_key,
        trading_date=trading_date,
        symbol=symbol,
        side=_normalize_side(_first_non_empty(order.get("action"), order.get("side"), payload.get("side"))),
        quantity=_parse_int(_first_non_empty(order.get("quantity"), order.get("qty"), payload.get("quantity"))),
        price=_parse_float(_first_non_empty(order.get("price"), payload.get("price"))),
        normalized_status=normalized_status,
        raw_status=raw_status,
        review_reason=",".join(review_reasons),
        raw={"stat": str(stat), "msg": payload},
    )


def _empty_execution_sync_snapshot() -> dict[str, Any]:
    return {
        "broker_trades": [],
        "open_positions": [],
        "results": [],
        "callback_events": [],
        "lifecycle_decisions": [],
        "callback_event_keys": [],
        "callback_status_by_order": {},
        "callback_ordering_issues": [],
        "custom_field_map": {},
    }


def _to_plain_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in {None, ""}:
            return value
    return None


def _resolve_callback_idempotency_key(
    explicit_idempotency_key: str,
    custom_field: str,
    custom_field_map: dict[str, str],
) -> str:
    if explicit_idempotency_key:
        return explicit_idempotency_key
    if custom_field in custom_field_map:
        return custom_field_map[custom_field]
    if ":" in custom_field:
        return custom_field
    return ""


def _normalize_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"buy", "b", "action.buy"}:
        return "buy"
    if text in {"sell", "s", "action.sell"}:
        return "sell"
    return text


def _parse_int(value: Any) -> int:
    if value in {None, ""}:
        return 0
    return int(value)


def _parse_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


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

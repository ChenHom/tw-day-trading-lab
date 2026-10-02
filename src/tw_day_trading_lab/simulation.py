from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time
from pathlib import Path
from typing import Any, Protocol

from .ledger import DuplicateIntentError, OrderIntent, PaperLedger
from .models import CandidateScore
from .performance import RollingPerformanceTracker
from .shioaji_compat import login_simulation_api, shioaji_enum_value, stock_contract


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
class SimulationPlanItem:
    signal: SignalIntent
    risk_decision: RiskDecision
    candidate_source: str
    risk_decision_reason: str
    limit_price_source: str
    quantity_source: str
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": asdict(self.signal),
            "risk_decision": asdict(self.risk_decision),
            "candidate_source": self.candidate_source,
            "risk_decision_reason": self.risk_decision_reason,
            "limit_price_source": self.limit_price_source,
            "quantity_source": self.quantity_source,
            "blocked_reason": self.blocked_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SimulationPlanItem":
        return cls(
            signal=SignalIntent.from_dict(data["signal"]),
            risk_decision=RiskDecision.from_dict(data["risk_decision"]),
            candidate_source=str(data["candidate_source"]),
            risk_decision_reason=str(data["risk_decision_reason"]),
            limit_price_source=str(data["limit_price_source"]),
            quantity_source=str(data["quantity_source"]),
            blocked_reason=data.get("blocked_reason"),
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
class ProductionReadinessPolicy:
    trading_date: str
    current_time: str | None = None
    allow_live_trading: bool = False
    manual_approval_token: str = ""
    expected_manual_approval_token: str = ""
    max_pending_orders: int = 0
    require_cancel_retry_plan: bool = True


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


@dataclass(frozen=True)
class Alert:
    alert_id: str
    run_id: str
    severity: str  # 'info' | 'warning' | 'error' | 'critical'
    category: str  # 'candidate_quality' | 'execution_health' | 'readiness' | 'reporting' | 'regression'
    dedupe_key: str
    owner: str
    manual_action: str
    send_gate: bool
    sent_at: str | None = None
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_alerts_from_run(
    run_id: str,
    candidates: list[CandidateScore],
    simulation_results: list[SimulationResult],
    readiness_report: dict[str, Any],
) -> list[Alert]:
    """Generate alerts from a daily ops run based on data gaps, execution anomalies, and readiness blocker checks."""
    alerts = []

    # 1. Candidate Quality Alert
    degraded = [c for c in candidates if c.downgrade_reasons]
    if degraded:
        alerts.append(
            Alert(
                alert_id=f"alert-{run_id}-cand-degraded",
                run_id=run_id,
                severity="warning",
                category="candidate_quality",
                dedupe_key=f"cand_degraded_{run_id}",
                owner="research_analyst",
                manual_action="Review degraded candidate data files for gaps.",
                send_gate=False,
            )
        )

    # 2. Execution Health Alert
    needs_review_sim = [r for r in simulation_results if r.status in {"needs_review", "gate_blocked"}]
    if needs_review_sim:
        alerts.append(
            Alert(
                alert_id=f"alert-{run_id}-exec-review",
                run_id=run_id,
                severity="error",
                category="execution_health",
                dedupe_key=f"exec_review_{run_id}",
                owner="execution_operator",
                manual_action="Check execution sync store for mismatched order statuses.",
                send_gate=False,
            )
        )

    # 3. Readiness Alerts
    for item in readiness_report.get("alerts", []):
        alert_name = item.get("name", "unknown")
        severity_val = "error" if item.get("severity") == "blocker" else "warning"
        alerts.append(
            Alert(
                alert_id=f"alert-{run_id}-readiness-{alert_name}",
                run_id=run_id,
                severity=severity_val,
                category="readiness",
                dedupe_key=f"readiness_{alert_name}_{run_id}",
                owner="execution_operator",
                manual_action=f"Resolve readiness issue: {item.get('review_reason')}. Details: {item.get('detail')}",
                send_gate=False,
            )
        )

    return alerts


def send_alerts_telegram(
    alerts: list[Alert],
    *,
    dry_run: bool = False,
    log_path: Path | None = None,
    current_time: str | None = None,
) -> list[Alert]:
    """Send qualifying alerts via Telegram with 24h deduplication and severity gate."""
    import os
    import json
    import urllib.request
    import urllib.parse
    import dataclasses
    from datetime import datetime, timezone
    from pathlib import Path

    telegram_enabled = os.getenv("TELEGRAM_ENABLED", "").lower() in ("true", "1", "yes")

    if log_path is None:
        log_path = Path("data/telegram_send_log.json")

    send_log = {}
    if log_path.exists():
        try:
            send_log = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    updated_alerts = []

    if current_time is not None:
        try:
            now = datetime.fromisoformat(current_time)
        except Exception:
            now = datetime.now(timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    for alert in alerts:
        # 1. 只有 severity = "error" 或 "critical" 才發送
        if alert.severity not in ("error", "critical"):
            updated_alerts.append(alert)
            continue

        # 2. 檢查 dedupe
        is_deduped = False
        if alert.dedupe_key in send_log:
            try:
                sent_time = datetime.fromisoformat(send_log[alert.dedupe_key])
                # 如果小於 24 小時則 dedupe (86400 秒)
                if (now - sent_time).total_seconds() < 86400:
                    is_deduped = True
            except Exception:
                pass

        if is_deduped:
            updated_alerts.append(alert)
            continue

        # 3. 檢查 Telegram 啟用開關或 dry-run
        if not telegram_enabled or dry_run:
            updated_alerts.append(alert)
            continue

        # 4. 實際發送
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            print(f"TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing. Cannot send alert: {alert.alert_id}")
            updated_alerts.append(alert)
            continue

        msg = (
            f"⚠️ [Alert] {alert.category.upper()} - {alert.severity.upper()}\n"
            f"Run ID: {alert.run_id}\n"
            f"Manual Action: {alert.manual_action}"
        )

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        success = False
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res = json.loads(response.read().decode("utf-8"))
                if res.get("ok"):
                    success = True
                    print(f"Telegram alert sent successfully: {alert.alert_id}")
                else:
                    print(f"Telegram API error for alert {alert.alert_id}: {res}")
        except Exception as e:
            print(f"Failed to send Telegram alert {alert.alert_id}: {e}")

        if success:
            send_log[alert.dedupe_key] = now.isoformat()
            new_alert = dataclasses.replace(
                alert,
                send_gate=True,
                sent_at=now.isoformat()
            )
            updated_alerts.append(new_alert)
        else:
            updated_alerts.append(alert)

    # 寫入儲存 log
    if send_log and not dry_run and telegram_enabled:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps(send_log, indent=2), encoding="utf-8")
        except Exception:
            pass

    return updated_alerts


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    source_run_id: str
    failure_type: str  # 'data_issue' | 'candidate_quality' | 'risk_decision' | 'broker_callback_lifecycle' | 'reporting_issue'
    raw_source_payload: dict[str, Any]
    minimal_fixture_path: str
    expected_behavior: str
    red_command: str
    closing_test_command: str
    status: str  # 'open' | 'fixed' | 'wont_fix' | 'needs_manual_review'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)




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

    def fetch_contract_details(self, symbol: str) -> dict[str, Any] | None:
        """Query contract details from the broker."""

    def is_simulation(self) -> bool:
        """Return True if running in simulation-only mode."""


class ShioajiOrderGateway(Protocol):
    def login(self) -> dict[str, str]:
        """Return a gateway-owned Shioaji simulation session."""

    def place_order(
        self,
        session: dict[str, str],
        request: ShioajiOrderRequest,
    ) -> dict[str, Any]:
        """Submit a Shioaji-shaped request and return the raw order response."""

    def fetch_contract_details(self, symbol: str) -> dict[str, Any] | None:
        """Query contract details from Shioaji."""

    def is_simulation(self) -> bool:
        """Return True if gateway connects to a simulation environment."""


class ShioajiCancelGateway(Protocol):
    def login(self) -> dict[str, str]:
        """Return a gateway-owned Shioaji simulation session."""

    def cancel_order(
        self,
        session: dict[str, str],
        broker_order_id: str,
        order_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Cancel a Shioaji simulation order and return the raw cancel response."""


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

    def fetch_contract_details(self, symbol: str) -> dict[str, Any] | None:
        return {
            "symbol": symbol,
            "reference": 900.0,
            "limit_up": 990.0,
            "limit_down": 810.0,
        }

    def is_simulation(self) -> bool:
        return True


class ShioajiOrderRequestBroker:
    """Adapt a Shioaji-shaped gateway to the internal simulation broker protocol."""

    def __init__(self, gateway: ShioajiOrderGateway) -> None:
        self._gateway = gateway
        self.last_request: ShioajiOrderRequest | None = None
        self.last_response: dict[str, Any] | None = None

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
        self.last_request = request

        self.last_response = dict(response)
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

    def fetch_contract_details(self, symbol: str) -> dict[str, Any] | None:
        if hasattr(self._gateway, "fetch_contract_details"):
            return self._gateway.fetch_contract_details(symbol)
        return None

    def is_simulation(self) -> bool:
        if hasattr(self._gateway, "is_simulation"):
            return self._gateway.is_simulation()
        return True



class ShioajiSdkSimulationGateway:
    """Small Shioaji SDK boundary that can be tested with a fake API object."""

    def __init__(
        self,
        api: Any,
        api_key: str,
        secret_key: str,
        *,
        account: Any = None,
        fetch_contract: bool = True,
        subscribe_trade: bool = True,
        order_factory: Callable[..., Any] | None = None,
    ) -> None:
        if getattr(api, "simulation", True) is not True:
            raise ValueError("ShioajiSdkSimulationGateway requires api.simulation=True")
        self._api = api
        self._api_key = api_key
        self._secret_key = secret_key
        self._account = account
        self._fetch_contract = fetch_contract
        self._subscribe_trade = subscribe_trade
        self._order_factory = order_factory
        self._raw_trade_by_order_id: dict[str, Any] = {}

    def login(self) -> dict[str, str]:
        accounts = login_simulation_api(
            self._api.login,
            api_key=self._api_key,
            secret_key=self._secret_key,
            fetch_contract=self._fetch_contract,
            subscribe_trade=self._subscribe_trade,
        )
        if self._account is None:
            self._account = getattr(self._api, "stock_account", None)
            if self._account is None and accounts:
                self._account = accounts[0]

        # Automatically activate CA cert (Sinopac.pfx) if variables are set
        import os
        ca_path = os.getenv("CERT_PATH") or os.getenv("SJ_CA_PATH") or ""
        if not ca_path:
            # Fallback to Sinopac.pfx in current working directory
            fallback_path = os.path.join(os.getcwd(), "Sinopac.pfx")
            if os.path.exists(fallback_path):
                ca_path = fallback_path

        ca_passwd = os.getenv("CA_PASSWORD") or os.getenv("SJ_CA_PASSWD") or ""
        ca_id = os.getenv("CA_ID", "")

        if ca_path and ca_passwd and ca_id:
            if not os.path.isabs(ca_path):
                ca_path = os.path.abspath(ca_path)
            if os.path.exists(ca_path):
                self._api.activate_ca(
                    ca_path=ca_path,
                    ca_passwd=ca_passwd,
                    person_id=ca_id,
                )
        return {
            "mode": "simulation",
            "session_id": "shioaji-sdk",
            "status": "logged_in",
            "accounts": [str(a) for a in accounts],
            "default_account": str(self._account) if self._account else "",
        }

    def fetch_contract_details(self, symbol: str) -> dict[str, Any] | None:
        try:
            contract = stock_contract(self._api, symbol)
            if contract is None:
                return None
            return {
                "symbol": symbol,
                "reference": getattr(contract, "reference", None),
                "limit_up": getattr(contract, "limit_up", None),
                "limit_down": getattr(contract, "limit_down", None),
            }
        except Exception:
            return None

    def is_simulation(self) -> bool:
        return getattr(self._api, "simulation", False) is True


    def place_order(
        self,
        session: dict[str, str],
        request: ShioajiOrderRequest,
    ) -> dict[str, Any]:
        contract = stock_contract(self._api, request.symbol)

        # Check if StockOrder class exists directly on shioaji module (Shioaji 1.5+)
        # Otherwise fallback to the deprecated api.Order class (Shioaji 1.3)
        import shioaji as sj  # type: ignore
        order_factory = self._order_factory or getattr(sj, "StockOrder", None)
        if order_factory is None:
            order_factory = self._api.Order

        order = order_factory(
            price=request.price if request.price is not None else 0,
            quantity=request.quantity,
            action=self._resolve_sdk_value("Action", request.side.capitalize()),
            price_type=self._resolve_sdk_value("StockPriceType", request.price_type),
            order_type=self._resolve_sdk_value("OrderType", request.order_type),
            account=self._account,
            custom_field=request.custom_field,
        )
        raw_trade = self._api.place_order(contract, order)
        response = _normalize_shioaji_place_order_response(raw_trade, request)
        if response["broker_order_id"]:
            self._raw_trade_by_order_id[response["broker_order_id"]] = raw_trade
        return response

    def cancel_order(
        self,
        session: dict[str, str],
        broker_order_id: str,
        order_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self._api, "cancel_order"):
            raise ValueError("Shioaji API object does not expose cancel_order")
        cancel_target = self._raw_trade_by_order_id.get(broker_order_id)
        if cancel_target is None:
            cancel_target = order_handle.get("raw") if order_handle else broker_order_id
        raw_cancel = self._api.cancel_order(cancel_target)
        return {
            "broker_order_id": broker_order_id,
            "status": str(_first_non_empty(_to_plain_mapping(raw_cancel).get("status"), "cancel_requested")),
            "raw": _to_plain_mapping(raw_cancel),
        }

    def _resolve_sdk_value(self, enum_name: str, member_name: str) -> Any:
        try:
            import shioaji as sj  # type: ignore

            return shioaji_enum_value(sj, enum_name, member_name)
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
    normalized = (
        raw_status.strip()
        .lower()
        .replace("_", "")
        .replace(" ", "")
        .replace(".", "")
    )
    status_map = {
        "filled": "filled",
        "partfilled": "partial_filled",
        "partialfilled": "partial_filled",
        "submitted": "submitted",
        "new": "submitted",
        "pendingsubmit": "submitted",
        "statuspendingsubmit": "submitted",
        "pending": "submitted",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "cancel": "cancelled",
        "cancelrequested": "submitted",
        "rejected": "rejected",
        "failed": "rejected",
        "statusfailed": "rejected",
    }
    return status_map.get(normalized, "needs_review")


class ShioajiSimulationAdapter:
    def __init__(self, broker: SimulationBroker, ledger: PaperLedger) -> None:
        self._broker = broker
        self._ledger = ledger
        self._session: dict[str, str] | None = None

    def execute(
        self,
        signal: SignalIntent,
        decision: RiskDecision,
        *,
        current_time: str | None = None,
        allow_outside_session: bool = False,
        max_quantity_cap: int = 1000,
        bypass_gates: bool = True,
    ) -> SimulationResult:
        if not decision.approved:
            return SimulationResult(
                status="risk_rejected",
                sample_type="simulation",
                expectancy_eligible=False,
                signal=signal,
                risk_decision=decision,
                review_reason=decision.reason,
            )

        if not bypass_gates:
            open_count = len(self._ledger._open_keys) if hasattr(self._ledger, "_open_keys") else 0
            gate_res = check_pre_order_gates(
                self._broker,
                signal,
                decision,
                current_time=current_time,
                allow_outside_session=allow_outside_session,
                max_quantity_cap=max_quantity_cap,
                open_positions_count=open_count,
            )
            if not gate_res["approved"]:
                return SimulationResult(
                    status="gate_blocked",
                    sample_type="simulation",
                    expectancy_eligible=False,
                    signal=signal,
                    risk_decision=decision,
                    review_reason=";".join(gate_res["blocked_reasons"]),
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
        if trade.status == "submitted":
            return "submitted", ""
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
        if normalized_status == "filled" and not ledger.has_open_key(trade.idempotency_key):
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


def is_terminal_callback_status(normalized_status: str) -> bool:
    """Return whether a status should not be superseded without manual review."""
    return normalized_status in {"filled", "cancelled", "rejected"}


class FileExecutionSyncStore:
    """Persist simulation execution state for restart reconciliation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

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
            "shioaji_order_handles": dict(raw.get("shioaji_order_handles", {})),
            "cancel_results": list(raw.get("cancel_results", [])),
        }

    def record_result(self, result: SimulationResult) -> None:
        def mutate(snapshot: dict[str, Any]) -> None:
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

        self._mutate_snapshot(mutate)

    def record_custom_field_mapping(self, custom_field: str, idempotency_key: str) -> None:
        def mutate(snapshot: dict[str, Any]) -> None:
            snapshot["custom_field_map"][custom_field] = idempotency_key

        self._mutate_snapshot(mutate)

    def record_order_handle(
        self,
        *,
        broker_order_id: str,
        idempotency_key: str,
        response: dict[str, Any],
    ) -> None:
        def mutate(snapshot: dict[str, Any]) -> None:
            snapshot["shioaji_order_handles"][broker_order_id] = {
                "broker_order_id": broker_order_id,
                "idempotency_key": idempotency_key,
                "raw": _json_safe(response.get("raw", response)),
            }

        self._mutate_snapshot(mutate)

    def record_cancel_result(
        self,
        *,
        broker_order_id: str,
        response: dict[str, Any],
    ) -> None:
        def mutate(snapshot: dict[str, Any]) -> None:
            snapshot["cancel_results"].append(
                {
                    "broker_order_id": broker_order_id,
                    "status": str(response.get("status") or ""),
                    "raw": _json_safe(response.get("raw", response)),
                }
            )

        self._mutate_snapshot(mutate)

    def record_callback_event(self, event: ExecutionCallbackEvent) -> bool:
        def mutate(snapshot: dict[str, Any]) -> bool:
            event_key = build_callback_event_key(event)
            if event_key in set(snapshot["callback_event_keys"]):
                return False
            order_key = build_callback_order_key(event)
            current_status = snapshot["callback_status_by_order"].get(order_key)
            if (
                current_status
                and current_status != event.normalized_status
                and is_terminal_callback_status(current_status)
                and is_terminal_callback_status(event.normalized_status)
            ):
                snapshot["callback_ordering_issues"].append(
                    {
                        "reason": "terminal_state_conflict",
                        "order_key": order_key,
                        "event_key": event_key,
                        "incoming_status": event.normalized_status,
                        "current_status": current_status,
                    }
                )
                return False
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
                return False
            snapshot["callback_event_keys"].append(event_key)
            snapshot["callback_status_by_order"][order_key] = event.normalized_status
            snapshot["callback_events"].append(event.to_dict())
            snapshot["lifecycle_decisions"].append(classify_execution_lifecycle(event).to_dict())
            trade = event.to_broker_trade()
            if trade:
                snapshot["broker_trades"].append(trade.to_dict())
            return True

        return self._mutate_snapshot(mutate)

    def _mutate_snapshot(self, mutate: Callable[[dict[str, Any]], Any]) -> Any:
        with self._exclusive_lock():
            snapshot = self.load_snapshot()
            result = mutate(snapshot)
            self._write_snapshot(snapshot)
            return result

    @contextmanager
    def _exclusive_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _write_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


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


def validate_live_approval_token_in_policy(policy: ProductionReadinessPolicy) -> bool:
    """Verify manual approval token with HMAC-SHA256."""
    import os
    secret = os.getenv("LIVE_APPROVAL_SECRET", "")
    if not secret:
        return bool(policy.manual_approval_token) and policy.manual_approval_token == policy.expected_manual_approval_token
    from .live import validate_hmac_token
    return validate_hmac_token(policy.manual_approval_token, secret)


def build_production_readiness_report(
    snapshot: dict[str, Any],
    policy: ProductionReadinessPolicy,
) -> dict[str, Any]:
    """Build the P7 production readiness gate report from execution sync state."""
    callback_events = [dict(row) for row in snapshot.get("callback_events", [])]
    lifecycle_decisions = [dict(row) for row in snapshot.get("lifecycle_decisions", [])]
    ordering_issues = [dict(row) for row in snapshot.get("callback_ordering_issues", [])]
    cancel_results = [dict(row) for row in snapshot.get("cancel_results", [])]
    broker_trades = [dict(row) for row in snapshot.get("broker_trades", [])]

    terminal_order_keys = {
        _readiness_order_key(row)
        for row in callback_events
        if str(row.get("normalized_status") or "") in {"filled", "cancelled", "rejected"}
    }
    submitted_order_keys = {
        _readiness_order_key(row)
        for row in callback_events + broker_trades
        if normalize_broker_status(
            str(row.get("raw_status") or row.get("normalized_status") or row.get("status") or "")
        )
        == "submitted"
    }
    pending_order_keys = sorted(key for key in submitted_order_keys if key not in terminal_order_keys)
    partial_fill_decisions = [
        row for row in lifecycle_decisions if str(row.get("normalized_status") or "") == "partial_filled"
    ]
    failed_cancel_results = [
        row
        for row in cancel_results
        if _readiness_cancel_status(row) not in {"cancelled", "submitted"}
    ]

    checks = [
        _readiness_check(
            "formal_live_gate",
            policy.allow_live_trading
            and validate_live_approval_token_in_policy(policy),
            "live_trading_requires_explicit_manual_approval",
            severity="blocker",
        ),
        _readiness_check(
            "regular_session_policy",
            policy.current_time is None or is_regular_day_order_smoke_time(policy.current_time),
            "outside_regular_session",
            severity="blocker",
        ),
        _readiness_check(
            "pending_order_limit",
            len(pending_order_keys) <= policy.max_pending_orders,
            "pending_orders_require_manual_reconciliation",
            severity="blocker",
            detail={"pending_orders": len(pending_order_keys), "max_pending_orders": policy.max_pending_orders},
        ),
        _readiness_check(
            "partial_fill_policy",
            not partial_fill_decisions,
            "partial_fill_requires_manual_policy",
            severity="blocker",
            detail={"partial_fills": len(partial_fill_decisions)},
        ),
        _readiness_check(
            "callback_ordering",
            not ordering_issues,
            "callback_ordering_issues_present",
            severity="blocker",
            detail={"ordering_issues": len(ordering_issues)},
        ),
        _readiness_check(
            "cancel_retry_plan",
            not policy.require_cancel_retry_plan or not (pending_order_keys or failed_cancel_results),
            "cancel_retry_plan_required",
            severity="needs_review",
            detail={
                "pending_orders": len(pending_order_keys),
                "failed_cancel_results": len(failed_cancel_results),
            },
        ),
    ]
    blockers = [check for check in checks if check["status"] == "blocked"]
    needs_review = [check for check in checks if check["status"] == "needs_review"]
    alerts = [
        {
            "severity": check["severity"],
            "name": check["name"],
            "review_reason": check["review_reason"],
            "detail": check.get("detail", {}),
        }
        for check in checks
        if check["status"] != "ok"
    ]
    status = "blocked" if blockers else "needs_review" if needs_review else "ready"
    return {
        "trading_date": policy.trading_date,
        "mode": "production_readiness",
        "status": status,
        "summary": {
            "checks": len(checks),
            "ok": sum(1 for check in checks if check["status"] == "ok"),
            "blocked": len(blockers),
            "needs_review": len(needs_review),
            "alerts": len(alerts),
            "pending_orders": len(pending_order_keys),
            "partial_fills": len(partial_fill_decisions),
            "ordering_issues": len(ordering_issues),
        },
        "checks": checks,
        "alerts": alerts,
        "manual_actions": _readiness_manual_actions(alerts),
        "live_execution_allowed": status == "ready",
    }


def render_production_readiness_markdown(report: dict[str, Any]) -> str:
    """Render a compact operator-facing P7 readiness report."""
    summary = dict(report.get("summary", {}))
    lines = [
        f"# Production Readiness - {report.get('trading_date', '')}",
        "",
        f"- status: {report.get('status', '')}",
        f"- live execution allowed: {str(report.get('live_execution_allowed', False)).lower()}",
        f"- checks: {summary.get('ok', 0)} ok / {summary.get('blocked', 0)} blocked / {summary.get('needs_review', 0)} needs_review",
        f"- pending orders: {summary.get('pending_orders', 0)}",
        f"- partial fills: {summary.get('partial_fills', 0)}",
        f"- ordering issues: {summary.get('ordering_issues', 0)}",
        "",
        "## Alerts",
    ]
    alerts = list(report.get("alerts", []))
    if not alerts:
        lines.append("- none")
    else:
        for alert in alerts:
            lines.append(
                f"- {alert.get('severity', '')}: {alert.get('name', '')} - {alert.get('review_reason', '')}"
            )
    lines.extend(["", "## Manual Actions"])
    actions = list(report.get("manual_actions", []))
    if not actions:
        lines.append("- none")
    else:
        lines.extend(f"- {action}" for action in actions)
    return "\n".join(lines) + "\n"


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
    operation = _to_plain_mapping(payload.get("operation", {}))

    raw_status = str(
        _first_non_empty(
            status.get("status"),
            status.get("order_status"),
            operation.get("op_type"),
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


def run_callback_sequence_smoke(
    *,
    trading_date: str,
    callbacks: list[dict[str, Any]],
    store: FileExecutionSyncStore,
) -> dict[str, Any]:
    """Replay a callback sequence through the execution sync store contract."""
    events: list[dict[str, Any]] = []
    accepted_count = 0
    skipped_count = 0

    for index, callback in enumerate(callbacks, start=1):
        if not isinstance(callback, dict):
            raise ValueError("callback sequence items must be JSON objects")
        snapshot = store.load_snapshot()
        event = normalize_shioaji_order_callback(
            callback.get("stat", ""),
            callback.get("msg", {}),
            trading_date=trading_date,
            custom_field_map=snapshot["custom_field_map"],
        )
        accepted = store.record_callback_event(event)
        if accepted:
            accepted_count += 1
        else:
            skipped_count += 1
        events.append(
            {
                "index": index,
                "accepted": accepted,
                "normalized_status": event.normalized_status,
                "broker_order_id": event.broker_order_id,
                "idempotency_key": event.idempotency_key,
                "review_reason": event.review_reason,
            }
        )

    final_snapshot = store.load_snapshot()
    return {
        "trading_date": trading_date,
        "summary": {
            "total": len(callbacks),
            "accepted": accepted_count,
            "skipped": skipped_count,
        },
        "events": events,
        "ordering_issues": final_snapshot["callback_ordering_issues"],
        "store_summary": {
            "callback_events": len(final_snapshot["callback_events"]),
            "broker_trades": len(final_snapshot["broker_trades"]),
            "lifecycle_decisions": len(final_snapshot["lifecycle_decisions"]),
            "ordering_issues": len(final_snapshot["callback_ordering_issues"]),
        },
    }


def run_gated_shioaji_simulation_login_smoke(
    *,
    api_factory: Callable[..., Any],
    api_key: str | None,
    secret_key: str | None,
    enabled: bool,
    fetch_contract: bool = False,
    subscribe_trade: bool = False,
) -> dict[str, Any]:
    """Run an explicitly gated Shioaji simulation login smoke."""
    report: dict[str, Any] = {
        "status": "blocked",
        "mode": "shioaji_simulation_login",
        "checks": {
            "gate_enabled": enabled,
            "credentials_present": bool(api_key and secret_key),
            "simulation_only": True,
            "orders_allowed": False,
            "fetch_contract": fetch_contract,
            "subscribe_trade": subscribe_trade,
        },
        "side_effects": [],
        "review_reason": "",
    }
    if not enabled:
        report["review_reason"] = "enable_login_smoke_required"
        return report
    if not api_key or not secret_key:
        report["review_reason"] = "shioaji_credentials_required"
        return report

    report["side_effects"] = ["login"]
    api = api_factory(simulation=True)
    gateway = ShioajiSdkSimulationGateway(
        api=api,
        api_key=api_key,
        secret_key=secret_key,
        fetch_contract=fetch_contract,
        subscribe_trade=subscribe_trade,
    )
    session = gateway.login()
    report["status"] = "ok"
    report["session"] = session
    return report


def run_gated_shioaji_callback_stream_smoke(
    *,
    api: Any,
    store: FileExecutionSyncStore,
    trading_date: str,
    enabled: bool,
) -> dict[str, Any]:
    """Register the callback stream behind an explicit smoke-test gate."""
    report: dict[str, Any] = {
        "status": "blocked",
        "mode": "shioaji_callback_stream",
        "trading_date": trading_date,
        "checks": {
            "gate_enabled": enabled,
            "simulation_api": getattr(api, "simulation", None) is True,
        },
        "side_effects": ["set_order_callback"] if enabled else [],
        "review_reason": "",
    }
    if not enabled:
        report["review_reason"] = "enable_callback_stream_required"
        return report

    stream = ShioajiCallbackStream(api=api, store=store, trading_date=trading_date)
    stream.start()
    snapshot = store.load_snapshot()
    report["status"] = "registered"
    report["callback_count"] = stream.callback_count
    report["store_summary"] = {
        "callback_events": len(snapshot["callback_events"]),
        "broker_trades": len(snapshot["broker_trades"]),
        "lifecycle_decisions": len(snapshot["lifecycle_decisions"]),
        "ordering_issues": len(snapshot["callback_ordering_issues"]),
    }
    return report


def is_regular_day_order_smoke_time(value: str | None = None) -> bool:
    """Return whether a smoke order is inside the regular intraday order window."""
    current = _parse_hhmm(value) if value else datetime.now().time()
    return time(9, 0) <= current <= time(13, 20)


def _read_jsonl_helper(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def check_pre_order_gates(
    broker: SimulationBroker,
    signal: SignalIntent,
    decision: RiskDecision,
    *,
    current_time: str | None = None,
    allow_outside_session: bool = False,
    max_quantity_cap: int = 1000,
    open_positions_count: int = 0,
    equity: float = 1_000_000.0,
    total_open_risk: float = 0.0,
) -> dict[str, Any]:
    """Run pre-order gates and return checks status and blocked reasons."""
    blocked_reasons = []

    # 1. Trading day (weekday check)
    try:
        dt = datetime.strptime(signal.trading_date, "%Y-%m-%d")
        is_trading_day = dt.weekday() < 5
    except Exception:
        is_trading_day = False
    if not is_trading_day:
        blocked_reasons.append("not_a_trading_day")

    # 2. Regular session and 13:25-13:30 Special Matching Rule
    session_allowed = allow_outside_session or is_regular_day_order_smoke_time(current_time)
    if not session_allowed:
        blocked_reasons.append("outside_regular_session")

    if current_time and not allow_outside_session:
        try:
            parts = current_time.split(":")
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                if hour == 13 and 25 <= minute <= 30:
                    blocked_reasons.append("final_match_collection_period")
        except Exception:
            pass

    # 3. Contract loaded, Reference price, Limit up/down
    contract = broker.fetch_contract_details(signal.symbol)
    limit_down = None
    limit_up = None
    ref_price = None
    if contract is None:
        blocked_reasons.append("contract_not_loaded")
        is_contract_loaded = False
    else:
        is_contract_loaded = True
        ref_price = contract.get("reference")
        limit_up = contract.get("limit_up")
        limit_down = contract.get("limit_down")

        # Check limits
        price = decision.price if decision.price is not None else signal.price
        if price is None:
            blocked_reasons.append("price_is_none")
        else:
            if limit_down is not None and price < limit_down:
                blocked_reasons.append("price_below_limit_down")
            if limit_up is not None and price > limit_up:
                blocked_reasons.append("price_above_limit_up")

            # Limit Up / Limit Down micro-structure checks
            side = signal.side.lower()
            if side == "buy" and limit_up is not None and price >= limit_up:
                blocked_reasons.append("buying_at_limit_up_is_forbidden")
            if side == "sell" and limit_down is not None and price <= limit_down:
                blocked_reasons.append("selling_at_limit_down_is_forbidden")

        # Check margin short-selling eligibility
        if signal.side.lower() == "sell":
            short_eligible = contract.get("short_selling_eligible", True)
            if not short_eligible:
                blocked_reasons.append("short_selling_not_eligible")

    # 4. Quantity cap and Portfolio limits
    quantity = decision.quantity if decision.quantity is not None else signal.quantity
    if quantity <= 0:
        blocked_reasons.append("quantity_must_be_positive")
    elif quantity > max_quantity_cap:
        blocked_reasons.append("quantity_exceeds_cap")

    if open_positions_count >= 3:
        blocked_reasons.append("max_concurrent_positions_limit_reached")

    price = decision.price if decision.price is not None else signal.price
    if price is not None and quantity > 0:
        trade_risk = 0.01 * price * quantity
        if total_open_risk + trade_risk > equity * 0.06:
            blocked_reasons.append("max_total_exposure_limit_exceeded")

    # 5. Simulation-only broker boundary
    is_simulation = False
    if hasattr(broker, "is_simulation"):
        is_simulation = broker.is_simulation()
    elif hasattr(broker, "_gateway") and hasattr(broker._gateway, "_api"):
        is_simulation = getattr(broker._gateway._api, "simulation", False) is True
    else:
        is_simulation = True

    if not is_simulation:
        blocked_reasons.append("live_broker_forbidden_in_simulation")

    approved = len(blocked_reasons) == 0
    return {
        "approved": approved,
        "is_trading_day": is_trading_day,
        "is_regular_session": session_allowed,
        "is_contract_loaded": is_contract_loaded,
        "reference_price": ref_price,
        "limit_down": limit_down,
        "limit_up": limit_up,
        "is_simulation_only": is_simulation,
        "blocked_reasons": blocked_reasons,
    }


def build_simulation_plan(
    *,
    trading_date: str,
    candidate_date: str,
    candidates: list[CandidateScore],
    cache_dir: Path,
    candidate_source: str,
    equity: float = 1_000_000.0,
    risk_pct: float = 0.01,
    stop_pct: float = 0.01,
    use_fixed_fractional: bool = False,
    performance_tracker: RollingPerformanceTracker | None = None,
) -> list[SimulationPlanItem]:
    """Build a simulation plan from candidates using the plan builder contract."""
    plan: list[SimulationPlanItem] = []

    multiplier = 1.0
    tracker_drawdown_limit = 0.05
    if performance_tracker is not None:
        multiplier = performance_tracker.get_risk_multiplier(max_drawdown_limit=tracker_drawdown_limit)

    for candidate in candidates:
        symbol = candidate.symbol
        price_file = cache_dir / "finmind" / "TaiwanStockPrice" / candidate_date / f"{symbol}.jsonl"
        close_price = None
        limit_price_source = "not_found"

        if price_file.exists():
            try:
                rows = _read_jsonl_helper(price_file)
                if rows:
                    close_price = float(rows[-1]["close"])
                    limit_price_source = "close_price"
            except Exception:
                pass

        blocked_reason = None
        risk_decision_reason = "risk_ok"
        approved = True

        if not candidate.next_day_actionable:
            approved = False
            risk_decision_reason = "not_actionable"
            blocked_reason = "not_actionable"
        elif close_price is None:
            approved = False
            risk_decision_reason = "close_price_missing"
            blocked_reason = "close_price_missing"

        # Position Sizing
        if approved and close_price is not None:
            if multiplier == 0.0:
                approved = False
                quantity = 0
                risk_decision_reason = "drawdown_limit_breached"
                blocked_reason = "drawdown_limit_breached"
                quantity_source = "disabled_by_performance"
            elif use_fixed_fractional:
                risk_amount = equity * risk_pct
                stop_price = close_price * (1.0 - stop_pct)
                risk_per_share = close_price - stop_price
                raw_qty = risk_amount / risk_per_share if risk_per_share > 0 else 0
                quantity = int(raw_qty // 1000) * 1000
                quantity = int(quantity * multiplier)

                if multiplier == 0.5:
                    risk_decision_reason = "downsized_due_to_negative_expectancy"

                if quantity * close_price > equity:
                    approved = False
                    quantity = 0
                    risk_decision_reason = "insufficient_equity"
                    blocked_reason = "insufficient_equity"
                elif quantity <= 0:
                    approved = False
                    quantity = 0
                    risk_decision_reason = "quantity_rounded_to_zero"
                    blocked_reason = "quantity_rounded_to_zero"

                quantity_source = "fixed_fractional"
            else:
                quantity = 1000
                quantity_source = "fixed_size_1000"
        else:
            quantity = 0 if not approved else 1000
            quantity_source = "fixed_size_1000" if approved else "not_approved"

        signal = SignalIntent(
            trading_date=trading_date,
            strategy_id="mvp",
            symbol=symbol,
            setup_id=candidate.archetype,
            side="buy",
            quantity=quantity if quantity > 0 else 1000,
            price=close_price,
        )

        risk_decision = RiskDecision(
            approved=approved,
            reason=risk_decision_reason,
            quantity=quantity if approved else None,
            price=close_price if approved else None,
        )

        plan.append(
            SimulationPlanItem(
                signal=signal,
                risk_decision=risk_decision,
                candidate_source=candidate_source,
                risk_decision_reason=risk_decision_reason,
                limit_price_source=limit_price_source,
                quantity_source=quantity_source,
                blocked_reason=blocked_reason,
            )
        )
    return plan



def run_gated_shioaji_order_request_smoke(
    *,
    gateway: ShioajiOrderGateway,
    store: FileExecutionSyncStore,
    signal: SignalIntent,
    decision: RiskDecision,
    enabled: bool,
    max_quantity: int = 1000,
    current_time: str | None = None,
    allow_outside_session: bool = False,
) -> dict[str, Any]:
    """Place one explicitly gated Shioaji simulation order and reconcile persisted state."""
    quantity = decision.quantity if decision.quantity is not None else signal.quantity
    price = decision.price if decision.price is not None else signal.price
    session_allowed = allow_outside_session or is_regular_day_order_smoke_time(current_time)
    report: dict[str, Any] = {
        "status": "blocked",
        "mode": "shioaji_order_request",
        "trading_date": signal.trading_date,
        "symbol": signal.symbol,
        "checks": {
            "gate_enabled": enabled,
            "orders_allowed": enabled,
            "risk_approved": decision.approved,
            "limit_price_present": price is not None,
            "quantity": quantity,
            "max_quantity": max_quantity,
            "quantity_within_limit": 0 < quantity <= max_quantity,
            "regular_session_allowed": session_allowed,
            "allow_outside_session": allow_outside_session,
        },
        "side_effects": [],
        "review_reason": "",
    }
    if not enabled:
        report["review_reason"] = "enable_order_smoke_required"
        return report
    if not decision.approved:
        report["review_reason"] = "risk_decision_not_approved"
        return report
    if price is None:
        report["review_reason"] = "limit_price_required"
        return report
    if quantity <= 0 or quantity > max_quantity:
        report["review_reason"] = "quantity_out_of_smoke_limit"
        return report
    if not session_allowed:
        report["review_reason"] = "outside_regular_session"
        return report

    intent = signal.to_order_intent()
    store.record_custom_field_mapping(
        build_shioaji_custom_field(intent.idempotency_key),
        intent.idempotency_key,
    )
    broker = ShioajiOrderRequestBroker(gateway)
    adapter = ShioajiSimulationAdapter(
        broker=broker,
        ledger=PaperLedger(),
    )
    result = adapter.execute(signal, decision)
    store.record_result(result)
    if result.trade and broker.last_response:
        store.record_order_handle(
            broker_order_id=result.trade.broker_order_id,
            idempotency_key=result.trade.idempotency_key,
            response=broker.last_response,
        )
    snapshot = store.load_snapshot()
    positions = ledger_positions_from_payload(snapshot["open_positions"])
    restart_report = build_restart_sync_report(
        broker_trades_from_payload(snapshot["broker_trades"]),
        positions,
        restore_ledger_from_positions(positions),
    )
    report["status"] = "ok" if result.status in {"simulated", "submitted"} else "needs_review"
    report["side_effects"] = ["login", "place_order"]
    report["result"] = {
        "status": result.status,
        "idempotency_key": result.order_intent.idempotency_key if result.order_intent else "",
        "broker_order_id": result.trade.broker_order_id if result.trade else "",
        "broker_status": result.trade.status if result.trade else "",
        "review_reason": result.review_reason,
    }
    report["store_summary"] = {
        "broker_trades": len(snapshot["broker_trades"]),
        "open_positions": len(snapshot["open_positions"]),
        "results": len(snapshot["results"]),
        "shioaji_order_handles": len(snapshot["shioaji_order_handles"]),
    }
    report["restart_sync"] = {
        "checked": restart_report["checked"],
        "matched": restart_report["matched"],
        "needs_review": restart_report["needs_review"],
    }
    return report


def run_gated_shioaji_cancel_smoke(
    *,
    gateway: ShioajiCancelGateway,
    broker_order_id: str,
    enabled: bool,
    store: FileExecutionSyncStore | None = None,
) -> dict[str, Any]:
    """Cancel one explicitly gated Shioaji simulation order."""
    report: dict[str, Any] = {
        "status": "blocked",
        "mode": "shioaji_cancel_order",
        "checks": {
            "gate_enabled": enabled,
            "broker_order_id_present": bool(broker_order_id),
        },
        "side_effects": [],
        "review_reason": "",
    }
    if not enabled:
        report["review_reason"] = "enable_cancel_smoke_required"
        return report
    if not broker_order_id:
        report["review_reason"] = "broker_order_id_required"
        return report

    snapshot = store.load_snapshot() if store else _empty_execution_sync_snapshot()
    order_handle = snapshot["shioaji_order_handles"].get(broker_order_id)
    session = gateway.login()
    try:
        response = gateway.cancel_order(session, broker_order_id, order_handle=order_handle)
    except Exception as error:
        report["status"] = "needs_review"
        report["side_effects"] = ["login", "cancel_order"]
        report["review_reason"] = "cancel_order_failed"
        report["result"] = {
            "broker_order_id": broker_order_id,
            "status": "needs_review",
            "error": str(error),
            "used_order_handle": bool(order_handle),
        }
        return report
    if store:
        store.record_cancel_result(broker_order_id=broker_order_id, response=response)
    report["status"] = "ok"
    report["side_effects"] = ["login", "cancel_order"]
    report["result"] = {
        "broker_order_id": str(response.get("broker_order_id") or broker_order_id),
        "status": str(response.get("status") or ""),
        "used_order_handle": bool(order_handle),
    }
    if store:
        final_snapshot = store.load_snapshot()
        report["store_summary"] = {
            "shioaji_order_handles": len(final_snapshot["shioaji_order_handles"]),
            "cancel_results": len(final_snapshot["cancel_results"]),
        }
    return report


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
        "shioaji_order_handles": {},
        "cancel_results": [],
    }


def _readiness_order_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("trading_date") or ""),
            str(row.get("broker_order_id") or ""),
            str(row.get("idempotency_key") or ""),
        ]
    )


def _readiness_check(
    name: str,
    ok: bool,
    review_reason: str,
    *,
    severity: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "ok" if ok else ("blocked" if severity == "blocker" else "needs_review"),
        "severity": severity,
        "review_reason": "" if ok else review_reason,
        "detail": detail or {},
    }


def _readiness_cancel_status(row: dict[str, Any]) -> str:
    raw = _to_plain_mapping(row.get("raw", {}))
    raw_status = _to_plain_mapping(raw.get("status", {}))
    return normalize_broker_status(
        str(
            _first_non_empty(
                raw_status.get("status"),
                row.get("status"),
                raw.get("status"),
            )
            or ""
        )
    )


def _readiness_manual_actions(alerts: list[dict[str, Any]]) -> list[str]:
    action_map = {
        "formal_live_gate": "Keep live trading disabled until an explicit manual approval token is provided.",
        "regular_session_policy": "Run production checks during regular session or define an approved outside-session procedure.",
        "pending_order_limit": "Reconcile or cancel pending broker orders before enabling live execution.",
        "partial_fill_policy": "Resolve partial fills manually and define the ledger mutation policy before production.",
        "callback_ordering": "Inspect callback ordering issues before trusting broker / ledger state.",
        "cancel_retry_plan": "Prepare cancel retry and escalation steps for unresolved submitted orders.",
    }
    actions: list[str] = []
    for alert in alerts:
        action = action_map.get(str(alert.get("name") or ""))
        if action and action not in actions:
            actions.append(action)
    return actions


def _to_plain_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        if isinstance(value, (dict, list, tuple)) and not value:
            continue
        if value != "":
            return value
    return None


def _parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


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

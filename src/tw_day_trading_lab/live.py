from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .simulation import SignalIntent, RiskDecision, BrokerTrade, OrderIntent


@dataclass(frozen=True)
class LiveGateCheckResult:
    allowed: bool
    blocked_reasons: list[str]


def generate_live_approval_token(secret: str, timestamp: int | None = None) -> str:
    """Generate a 5-minute valid HMAC-SHA256 token."""
    import hmac
    import hashlib
    import time
    ts = timestamp if timestamp is not None else int(time.time())
    ts_str = str(ts)
    token_hmac = hmac.new(
        secret.encode("utf-8"),
        ts_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{ts_str}:{token_hmac}"


def get_hmac_token_error_reason(token: str, secret: str, current_time_unix: int | None = None) -> str | None:
    """Validate token and return error reason, or None if valid."""
    import hmac
    import hashlib
    import time
    if not secret:
        return "live_approval_secret_missing"
    if not token:
        return "manual_approval_token_invalid_format"
    try:
        parts = token.split(":")
        if len(parts) != 2:
            return "manual_approval_token_invalid_format"
        ts_str, token_hmac = parts
        ts = int(ts_str)
        now = current_time_unix if current_time_unix is not None else int(time.time())

        expected = hmac.new(
            secret.encode("utf-8"),
            ts_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, token_hmac):
            return "manual_approval_token_hash_mismatch"
        if abs(now - ts) > 300:
            return "manual_approval_token_expired"
        return None
    except Exception:
        return "manual_approval_token_invalid_format"


def validate_hmac_token(token: str, secret: str, current_time_unix: int | None = None) -> bool:
    """Check if token is valid."""
    return get_hmac_token_error_reason(token, secret, current_time_unix) is None


class LiveShioajiBrokerAdapter:
    """Rigid security boundary and order gateway for live trading. Decoupled from simulation adapter."""

    def __init__(self, api: Any, expected_token_hash: str = "") -> None:
        # Crucial P11 rule: Never allow api.simulation = True in live execution!
        if getattr(api, "simulation", False) is True:
            raise ValueError("LiveShioajiBrokerAdapter forbids api.simulation=True")
        self._api = api
        self._expected_token_hash = expected_token_hash

    def check_live_execution_gate(
        self,
        *,
        allow_live_trading: bool,
        manual_approval_token: str,
        regression_dir: Path = Path("data/regression"),
        current_time_unix: int | None = None,
    ) -> LiveGateCheckResult:
        """Evaluate the strict entrance gates for live orders."""
        blocked_reasons = []

        # 1. Formal live gate check
        if not allow_live_trading:
            blocked_reasons.append("allow_live_trading_is_false")

        # 2. Token hmac verification or fallback
        secret = os.getenv("LIVE_APPROVAL_SECRET", "")
        if not secret and self._expected_token_hash:
            if len(manual_approval_token) < 32:
                blocked_reasons.append("manual_approval_token_is_too_weak_must_be_at_least_32_chars")
            import hashlib
            token_hash = hashlib.sha256(manual_approval_token.encode("utf-8")).hexdigest()
            if token_hash != self._expected_token_hash:
                blocked_reasons.append("manual_approval_token_hash_mismatch")
        elif not secret:
            blocked_reasons.append("live_approval_secret_missing")
        else:
            reason = get_hmac_token_error_reason(manual_approval_token, secret, current_time_unix)
            if reason:
                blocked_reasons.append(reason)

        # 4. Check for open regression cases
        if regression_dir.exists():
            for p in regression_dir.glob("*.json"):
                try:
                    case_data = json.loads(p.read_text(encoding="utf-8"))
                    if case_data.get("status") == "open":
                        blocked_reasons.append(f"open_regression_case_present_{case_data.get('case_id')}")
                except Exception:
                    pass

        allowed = len(blocked_reasons) == 0
        return LiveGateCheckResult(allowed=allowed, blocked_reasons=blocked_reasons)

    def place_live_order(
        self,
        signal: SignalIntent,
        decision: RiskDecision,
        gate_result: LiveGateCheckResult,
    ) -> BrokerTrade:
        """Place a live order after verifying the validated gate checks."""
        if not gate_result.allowed:
            raise PermissionError(f"Live order execution blocked: {';'.join(gate_result.blocked_reasons)}")

        # Safe placeholder boundary for live ordering
        print(f"PLACING REAL ORDER FOR SYMBOL: {signal.symbol}")
        return BrokerTrade(
            idempotency_key=signal.to_order_intent().idempotency_key,
            broker_order_id="live-order-id-placeholder",
            trading_date=signal.trading_date,
            symbol=signal.symbol,
            side=signal.side,
            quantity=decision.quantity if decision.quantity is not None else signal.quantity,
            price=decision.price if decision.price is not None else signal.price,
            status="submitted",
            raw_status="submitted",
            source="live"
        )

    def panic_cancel_all(self) -> dict[str, Any]:
        """Panic cancellation kill switch for all open orders."""
        cancelled_count = 0
        if hasattr(self._api, "stock_account") and hasattr(self._api, "cancel_order"):
            try:
                self._api.update_status(self._api.stock_account)
                for trade in self._api.list_trades():
                    if trade.status.status in {"Submitted", "PreSubmitted", "PartFilled"}:
                        self._api.cancel_order(trade)
                        cancelled_count += 1
            except Exception as e:
                print(f"Error during panic cancel: {e}")

        return {
            "status": "panic_executed",
            "cancelled_count": cancelled_count,
        }

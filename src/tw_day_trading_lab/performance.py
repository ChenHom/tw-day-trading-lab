from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

@dataclass
class TradeRecord:
    symbol: str
    entry_price: float
    exit_price: float
    realized_r: float
    pnl: float

class RollingPerformanceTracker:
    """
    Rolling performance tracker that monitors rolling expectancy and drawdown.
    Supports auto-downsizing or disabling when thresholds are breached.
    """
    def __init__(self, initial_equity: float = 1_000_000.0) -> None:
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.trades: list[TradeRecord] = []
        self.max_equity = initial_equity

    def add_trade(self, symbol: str, entry_price: float, exit_price: float, realized_r: float, pnl: float) -> None:
        trade = TradeRecord(symbol, entry_price, exit_price, realized_r, pnl)
        self.trades.append(trade)
        self.equity += pnl
        if self.equity > self.max_equity:
            self.max_equity = self.equity

    def get_rolling_expectancy(self, window: int = 20) -> float:
        if not self.trades:
            return 0.0
        recent = self.trades[-window:]
        return sum(t.realized_r for t in recent) / len(recent)

    def get_current_drawdown(self) -> float:
        if self.max_equity <= 0:
            return 0.0
        return (self.max_equity - self.equity) / self.max_equity

    def get_risk_multiplier(self, window: int = 20, max_drawdown_limit: float = 0.05) -> float:
        """
        Returns size multiplier:
        - 0.0: Disable strategy completely (drawdown limit hit)
        - 0.5: Scale down to 50% size (rolling expectancy < 0)
        - 1.0: Full size (otherwise)
        """
        # 1. Drawdown check
        if self.get_current_drawdown() >= max_drawdown_limit:
            return 0.0

        # 2. Expectancy check (only when we have at least 5 trades to avoid early scaling)
        if len(self.trades) >= 5:
            rolling_exp = self.get_rolling_expectancy(window)
            if rolling_exp < 0:
                return 0.5

        return 1.0

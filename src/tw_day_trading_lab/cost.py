from __future__ import annotations
from dataclasses import dataclass


def is_etf_symbol(symbol: str) -> bool:
    """台股 ETF/ETN（受益憑證）代號一律以 00 開頭；普通股四碼且不會以 00 開頭。

    這個判斷會改變稅率與升降單位兩件事，兩者都是成本的主導項，所以錯判不是
    小數點後的差別：0050 在 78 元用股票階梯會把 tick 算成 0.10，實際是 0.05。
    """
    return symbol.startswith("00")


@dataclass(frozen=True)
class TaiwanDayTradeCostModel:
    """来回交易成本模型 (Taiwan Day-Trading Cost Model)"""
    commission_rate: float = 0.001425       # 1.425‰
    commission_discount: float = 0.3        # 3折
    day_trade_tax_rate: float = 0.0015      # 股票當沖減半稅 0.15%
    # ETF（受益憑證）證交稅本來就是 0.1%，當沖不再減半。
    # 待查核：此處依一般券商費用表，尚未對照實際交割單驗證。
    etf_tax_rate: float = 0.001
    # 2026-08-19 量測 12 檔的 tick_type，有效價差落在 0.71-1.00 個 tick。
    # 買在賣價、賣在買價，一趟來回付的是「一次」價差，所以單邊 0.5 tick。
    slippage_ticks_per_side: float = 0.5

    def tick_size(self, price: float, *, etf: bool = False) -> float:
        """台股最小升降單位 (Tick Size)"""
        if etf:
            # ETF 只有兩級，不是股票那六級階梯。
            return 0.01 if price < 50.0 else 0.05
        if price < 10.0:
            return 0.01
        elif price < 50.0:
            return 0.05
        elif price < 100.0:
            return 0.10
        elif price < 500.0:
            return 0.50
        elif price < 1000.0:
            return 1.00
        else:
            return 5.00

    def round_trip_cost(self, entry_price: float, quantity: int = 1000, *, etf: bool = False) -> float:
        """計算一趟來回的總成本 (元/股)"""
        buy_comm = entry_price * self.commission_rate * self.commission_discount
        sell_comm = entry_price * self.commission_rate * self.commission_discount

        # Minimum commission is 20 TWD. Convert to per-share cost.
        if quantity > 0:
            min_comm_per_share = 20.0 / quantity
            buy_comm = max(buy_comm, min_comm_per_share)
            sell_comm = max(sell_comm, min_comm_per_share)

        sell_tax = entry_price * (self.etf_tax_rate if etf else self.day_trade_tax_rate)

        tick = self.tick_size(entry_price, etf=etf)
        slippage = tick * self.slippage_ticks_per_side * 2  # 買賣雙邊各滑價 ticks

        return buy_comm + sell_comm + sell_tax + slippage

    def cost_r(self, entry_price: float, stop_price: float, quantity: int = 1000,
               *, etf: bool = False) -> float:
        """將來回成本換算為 R 單位"""
        risk = abs(entry_price - stop_price)
        if risk <= 0:
            return float("inf")
        total_cost = self.round_trip_cost(entry_price, quantity, etf=etf)
        return round(total_cost / risk, 4)

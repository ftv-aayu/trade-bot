"""
Paper Trader
Simulates order execution using real live prices but a local fake balance.
No real API orders are placed. Drop-in replacement for RoostooClient order calls.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

TAKER_FEE = 0.001   # 0.1%  — MARKET orders (same as real exchange)
MAKER_FEE = 0.0005  # 0.05% — LIMIT orders  (same as real exchange)


@dataclass
class Position:
    coin: str
    qty: float
    avg_entry: float  # average buy price


class PaperTrader:
    def __init__(self, starting_usd: float = 50_000.0):
        self.starting_usd = starting_usd
        self.usd_balance: float = starting_usd
        self._positions: dict[str, Position] = {}  # coin → Position
        self._orders: list[dict] = []
        self._order_id_counter = 1

    # ------------------------------------------------------------------
    # Balance helpers (mirror RoostooClient interface)
    # ------------------------------------------------------------------

    def get_usd_balance(self) -> float:
        return self.usd_balance

    def get_coin_balance(self, coin: str) -> float:
        pos = self._positions.get(coin.upper())
        return pos.qty if pos else 0.0

    def balance(self) -> dict:
        """Return a fake balance dict in the same shape as the real API."""
        wallet = {
            "USD": {"Free": round(self.usd_balance, 4), "Lock": 0.0}
        }
        for coin, pos in self._positions.items():
            if pos.qty > 0:
                wallet[coin] = {"Free": round(pos.qty, 8), "Lock": 0.0}
        return {"Success": True, "ErrMsg": "", "SpotWallet": wallet}

    # ------------------------------------------------------------------
    # Order execution (fake — uses the price you pass in)
    # ------------------------------------------------------------------

    def place_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
    ) -> dict:
        """
        Simulate a market order fill at `price`.
        price must be provided — caller should pass the live ticker price.
        """
        if price is None or price <= 0:
            return {"Success": False, "ErrMsg": "price required for paper trading"}

        coin = pair.split("/")[0].upper()
        side = side.upper()
        order_type = order_type.upper()

        # Match real exchange: MARKET = taker (0.1%), LIMIT = maker (0.05%)
        fee_rate = TAKER_FEE if order_type == "MARKET" else MAKER_FEE
        order_value = quantity * price
        commission = order_value * fee_rate

        order_id = self._order_id_counter
        self._order_id_counter += 1
        ts = int(time.time() * 1000)

        if side == "BUY":
            total_cost = order_value + commission
            if total_cost > self.usd_balance:
                return {
                    "Success": False,
                    "ErrMsg": f"insufficient paper balance (need ${total_cost:.2f}, have ${self.usd_balance:.2f})"
                }
            self.usd_balance -= total_cost

            # Update position (average in)
            if coin in self._positions:
                existing = self._positions[coin]
                new_qty = existing.qty + quantity
                new_avg = (existing.qty * existing.avg_entry + quantity * price) / new_qty
                self._positions[coin] = Position(coin, new_qty, new_avg)
            else:
                self._positions[coin] = Position(coin, quantity, price)

            logger.info("[PAPER] BUY  %s qty=%.6f @ $%.4f  cost=$%.2f  fee=$%.4f  usd_left=$%.2f",
                        pair, quantity, price, order_value, commission, self.usd_balance)

        elif side == "SELL":
            pos = self._positions.get(coin)
            if not pos or pos.qty < quantity:
                held = pos.qty if pos else 0
                return {
                    "Success": False,
                    "ErrMsg": f"insufficient {coin} (have {held:.6f}, trying to sell {quantity:.6f})"
                }
            proceeds = order_value - commission
            self.usd_balance += proceeds
            pos.qty -= quantity

            pnl = (price - pos.avg_entry) * quantity
            logger.info("[PAPER] SELL %s qty=%.6f @ $%.4f  proceeds=$%.2f  fee=$%.4f  pnl=$%.2f  usd=$%.2f",
                        pair, quantity, price, proceeds, commission, pnl, self.usd_balance)

            if pos.qty <= 0:
                del self._positions[coin]

        else:
            return {"Success": False, "ErrMsg": f"unknown side: {side}"}

        order = {
            "Success": True,
            "OrderDetail": {
                "Pair": pair,
                "OrderID": order_id,
                "Status": "FILLED",
                "Role": "TAKER",
                "CreateTimestamp": ts,
                "FinishTimestamp": ts,
                "Side": side,
                "Type": order_type.upper(),
                "Price": price,
                "Quantity": quantity,
                "FilledQuantity": quantity,
                "FilledAverPrice": price,
                "CommissionChargeValue": round(commission, 6),
                "CommissionPercent": fee_rate,
            }
        }
        self._orders.append(order["OrderDetail"])
        return order

    # ------------------------------------------------------------------
    # Portfolio valuation
    # ------------------------------------------------------------------

    def portfolio_value(self, tickers: dict) -> float:
        """Total portfolio value in USD using live prices."""
        total = self.usd_balance
        for coin, pos in self._positions.items():
            pair = f"{coin}/USD"
            price = tickers.get(pair, {}).get("LastPrice", 0.0)
            total += pos.qty * price
        return total

    def positions_summary(self, tickers: dict) -> list[dict]:
        """Return list of open positions with live P&L."""
        result = []
        for coin, pos in self._positions.items():
            pair = f"{coin}/USD"
            price = tickers.get(pair, {}).get("LastPrice", 0.0)
            current_val = pos.qty * price
            cost_basis  = pos.qty * pos.avg_entry
            unrealized  = current_val - cost_basis
            unrealized_pct = (unrealized / cost_basis * 100) if cost_basis else 0
            result.append({
                "coin": coin,
                "qty": pos.qty,
                "avg_entry": pos.avg_entry,
                "current_price": price,
                "current_value": current_val,
                "unrealized_pnl": unrealized,
                "unrealized_pct": unrealized_pct,
            })
        return sorted(result, key=lambda x: x["current_value"], reverse=True)

    def order_history(self) -> list[dict]:
        return list(reversed(self._orders))

    def reset(self):
        self.usd_balance = self.starting_usd
        self._positions.clear()
        self._orders.clear()
        self._order_id_counter = 1

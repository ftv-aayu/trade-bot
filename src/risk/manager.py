"""
Risk Manager
Controls position sizing, max allocation per asset, and portfolio-level drawdown limits.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_position_pct: float = 0.15     # max 15% of portfolio per asset
    min_order_usd: float = 10.0        # minimum order value in USD
    max_drawdown_pct: float = 0.12     # halt trading if drawdown > 12%
    commission_rate: float = 0.001     # 0.1% taker fee
    reserve_pct: float = 0.05         # keep 5% of portfolio as cash reserve


class RiskManager:
    def __init__(self, initial_balance: float, config: RiskConfig = None):
        self.config = config or RiskConfig()
        self.initial_balance = initial_balance
        self.peak_balance = initial_balance
        self._halted = False

    # ------------------------------------------------------------------
    # Portfolio state updates
    # ------------------------------------------------------------------

    def update_balance(self, current_total_usd: float):
        """Call this each cycle with the current portfolio value (USD)."""
        if current_total_usd > self.peak_balance:
            self.peak_balance = current_total_usd

        drawdown = (self.peak_balance - current_total_usd) / self.peak_balance
        if drawdown >= self.config.max_drawdown_pct:
            if not self._halted:
                logger.warning(
                    "DRAWDOWN HALT: %.1f%% drawdown (peak=%.2f, now=%.2f)",
                    drawdown * 100,
                    self.peak_balance,
                    current_total_usd,
                )
            self._halted = True
        else:
            if self._halted:
                logger.info("Drawdown recovered — resuming trading.")
            self._halted = False

    def is_halted(self) -> bool:
        return self._halted

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def position_size_usd(self, available_usd: float, portfolio_value: float) -> float:
        """
        Return how many USD to allocate to a single trade.
        Respects max_position_pct and keeps a cash reserve.
        """
        if self._halted:
            return 0.0

        # How much of the portfolio we are allowed to allocate
        max_alloc = portfolio_value * self.config.max_position_pct

        # Leave a reserve
        spendable = available_usd * (1 - self.config.reserve_pct)

        size = min(max_alloc, spendable)

        if size < self.config.min_order_usd:
            logger.debug("Position size %.2f below minimum %.2f — skipping", size, self.config.min_order_usd)
            return 0.0

        return size

    def quantity_for_usd(self, usd_amount: float, price: float, amount_precision: int = 6) -> float:
        """Convert a USD budget to a coin quantity, accounting for commission."""
        if price <= 0:
            return 0.0
        # After commission the effective cost per coin is price * (1 + fee)
        effective_price = price * (1 + self.config.commission_rate)
        qty = usd_amount / effective_price
        # Round down to exchange precision
        factor = 10 ** amount_precision
        qty = int(qty * factor) / factor
        return qty

    def check_order(self, qty: float, price: float) -> bool:
        """Return True if an order meets minimum size requirements."""
        order_value = qty * price
        if order_value < self.config.min_order_usd:
            logger.debug("Order value %.2f USD below minimum — skipped", order_value)
            return False
        return True

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def drawdown(self, current_total_usd: float) -> float:
        """Current drawdown as a fraction (0.05 = 5%)."""
        if self.peak_balance == 0:
            return 0.0
        return max(0.0, (self.peak_balance - current_total_usd) / self.peak_balance)

    def total_return(self, current_total_usd: float) -> float:
        """Total return as a fraction from initial balance."""
        return (current_total_usd - self.initial_balance) / self.initial_balance

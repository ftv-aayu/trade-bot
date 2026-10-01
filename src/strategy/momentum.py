"""
Momentum Strategy
EMA crossover + RSI with proper filters:
  - Minimum hold time before allowing sell
  - Only sell at profit OR if stop-loss triggered
  - Stronger RSI thresholds
  - Trend confirmation (fast EMA must be meaningfully above slow)
  - No trading when market is broadly weak
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Signal = Literal["BUY", "SELL", "HOLD"]


@dataclass
class MomentumConfig:
    fast_ema: int   = 8
    slow_ema: int   = 21
    rsi_period: int = 14
    min_history: int = 50

    # Signal filters
    rsi_buy_max: float  = 60.0   # only buy when RSI < 60 (not overbought)
    rsi_buy_min: float  = 40.0   # only buy when RSI > 40 (not already oversold)
    rsi_sell_min: float = 45.0   # only sell on EMA cross when RSI > 45

    # EMA separation — how much fast must be above slow to count as a real crossover
    ema_separation_pct: float = 0.02  # fast EMA must be 0.02% above slow to BUY

    # Hold & profit filters
    min_hold_cycles: int   = 3      # must hold at least 3 cycles before selling on signal
    min_profit_pct: float  = 0.25   # must be +0.25% profit before selling on signal
    stop_loss_pct: float   = 2.0    # hard stop-loss at -2% regardless of signal
    take_profit_pct: float = 3.0    # take profit at +3% regardless of signal


@dataclass
class PairState:
    prices: deque = field(default_factory=lambda: deque(maxlen=200))
    last_signal: Signal = "HOLD"
    entry_price: float  = 0.0   # price when BUY was last signalled
    hold_cycles: int    = 0     # cycles since last BUY signal
    just_restored: bool = False # skip first BUY signal after DB restore


def _ema(prices: list, period: int) -> float:
    if len(prices) < period:
        return float("nan")
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def _rsi(prices: list, period: int) -> float:
    if len(prices) < period + 1:
        return 50.0
    relevant = prices[-(period + 1):]
    deltas = [relevant[i+1] - relevant[i] for i in range(len(relevant)-1)]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


class MomentumStrategy:
    def __init__(self, config: MomentumConfig = None):
        self.config = config or MomentumConfig()
        self._states: dict[str, PairState] = {}
        self._restoring: bool = False  # suppress stop-loss/take-profit during DB restore

    def _state(self, pair: str) -> PairState:
        if pair not in self._states:
            self._states[pair] = PairState()
        return self._states[pair]

    def update(self, pair: str, price: float) -> Signal:
        if price <= 0:
            return "HOLD"

        state  = self._state(pair)
        state.prices.append(price)
        prices = list(state.prices)
        cfg    = self.config

        if len(prices) < cfg.min_history:
            return "HOLD"

        # On first live tick after restore, skip all signals but clear the flag
        # so second tick onward behaves normally
        if state.just_restored:
            state.just_restored = False
            return "HOLD"

        fast      = _ema(prices, cfg.fast_ema)
        slow      = _ema(prices, cfg.slow_ema)
        rsi       = _rsi(prices, cfg.rsi_period)
        prev      = prices[:-1]
        prev_fast = _ema(prev, cfg.fast_ema)
        prev_slow = _ema(prev, cfg.slow_ema)

        # EMA separation as % of price
        separation_pct = ((fast - slow) / slow * 100) if slow > 0 else 0

        signal: Signal = "HOLD"

        # ── BUY conditions ────────────────────────────────────────────
        # 1. EMA crossover (fast crossed above slow)
        # 2. RSI in healthy buy zone (40–60)
        # 3. EMA separation meaningful (not just noise)
        # 4. Not already holding (caller checks coin_held)
        crossed_up = prev_fast <= prev_slow and fast > slow
        if (
            crossed_up
            and fast > slow                              # confirm fast is above slow now
            and cfg.rsi_buy_min <= rsi <= cfg.rsi_buy_max
            and separation_pct >= cfg.ema_separation_pct
            and not state.just_restored                  # skip phantom crossover on first live tick
        ):
            signal = "BUY"
            state.entry_price = price
            state.hold_cycles = 0

        # ── SELL conditions ───────────────────────────────────────────
        elif state.last_signal == "BUY" or state.hold_cycles > 0:
            state.hold_cycles += 1
            pnl_pct = ((price - state.entry_price) / state.entry_price * 100) if state.entry_price > 0 else 0

            # 1. Hard stop-loss — sell immediately regardless of hold time
            if not self._restoring and pnl_pct <= -cfg.stop_loss_pct:
                signal = "SELL"
                logger.warning("%s STOP-LOSS triggered: pnl=%.2f%%", pair, pnl_pct)

            # 2. Take profit — sell immediately at target
            elif not self._restoring and pnl_pct >= cfg.take_profit_pct:
                signal = "SELL"
                logger.info("%s TAKE-PROFIT triggered: pnl=%.2f%%", pair, pnl_pct)

            # 3. EMA cross down — but only sell if:
            #    - held long enough AND in profit (don't sell at a loss on signal)
            elif (
                prev_fast >= prev_slow and fast < slow   # crossed down
                and rsi > cfg.rsi_sell_min               # not oversold (bounce possible)
                and state.hold_cycles >= cfg.min_hold_cycles
                and pnl_pct >= cfg.min_profit_pct        # only sell if profitable
            ):
                signal = "SELL"
                logger.info("%s EMA-cross SELL: pnl=%.2f%% rsi=%.1f cycles=%d",
                            pair, pnl_pct, rsi, state.hold_cycles)

        if signal != "HOLD":
            logger.info("%s signal=%s | fast=%.6f slow=%.6f rsi=%.1f sep=%.4f%%",
                        pair, signal, fast, slow, rsi, separation_pct)

        if signal == "SELL":
            state.entry_price = 0.0
            state.hold_cycles = 0

        state.last_signal = signal
        return signal

    def notify_bought(self, pair: str, price: float):
        """Call this after a BUY order fills to record entry price."""
        state = self._state(pair)
        state.entry_price  = price
        state.hold_cycles  = 0
        state.last_signal  = "BUY"

    def notify_sold(self, pair: str):
        """Call this after a SELL order fills to reset state."""
        state = self._state(pair)
        state.entry_price = 0.0
        state.hold_cycles = 0
        state.last_signal = "HOLD"

    def indicators(self, pair: str) -> dict:
        state  = self._state(pair)
        prices = list(state.prices)
        cfg    = self.config
        if len(prices) < cfg.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}
        fast = _ema(prices, cfg.fast_ema)
        slow = _ema(prices, cfg.slow_ema)
        sep  = ((fast - slow) / slow * 100) if slow > 0 else 0
        return {
            "pair":             pair,
            "last_price":       prices[-1],
            "fast_ema":         round(fast, 6),
            "slow_ema":         round(slow, 6),
            "ema_sep_pct":      round(sep, 4),
            "rsi":              round(_rsi(prices, cfg.rsi_period), 2),
            "hold_cycles":      state.hold_cycles,
            "entry_price":      state.entry_price,
            "prices_collected": len(prices),
        }

    def reset(self, pair: str = None):
        if pair:
            self._states.pop(pair, None)
        else:
            self._states.clear()

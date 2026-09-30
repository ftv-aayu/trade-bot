"""
Momentum Strategy
Maintains a rolling price history per pair and generates BUY / SELL / HOLD signals
using EMA crossover + RSI confirmation.

Signal logic:
  BUY  — fast EMA crossed above slow EMA AND RSI < 70 (not overbought)
  SELL — fast EMA crossed below slow EMA AND RSI > 30 (not oversold)
  HOLD — everything else
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Signal = Literal["BUY", "SELL", "HOLD"]


@dataclass
class MomentumConfig:
    fast_ema: int = 5       # fast EMA window (candle ticks)
    slow_ema: int = 20      # slow EMA window
    rsi_period: int = 14    # RSI lookback
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    min_history: int = 25   # minimum prices needed before signalling


@dataclass
class PairState:
    prices: deque = field(default_factory=lambda: deque(maxlen=200))
    last_signal: Signal = "HOLD"


def _ema(prices: list[float], period: int) -> float:
    """Compute EMA of a price series. Returns the last EMA value."""
    if len(prices) < period:
        return float("nan")
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def _rsi(prices: list[float], period: int) -> float:
    """Compute RSI over the last `period+1` prices."""
    if len(prices) < period + 1:
        return 50.0  # neutral when insufficient data
    relevant = prices[-(period + 1):]
    deltas = [relevant[i + 1] - relevant[i] for i in range(len(relevant) - 1)]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class MomentumStrategy:
    def __init__(self, config: MomentumConfig = None):
        self.config = config or MomentumConfig()
        self._states: dict[str, PairState] = {}

    def _state(self, pair: str) -> PairState:
        if pair not in self._states:
            self._states[pair] = PairState()
        return self._states[pair]

    def update(self, pair: str, price: float) -> Signal:
        """
        Feed a new price tick for a pair and return the current signal.
        Call this every polling cycle for each pair.
        """
        if price <= 0:
            return "HOLD"

        state = self._state(pair)
        state.prices.append(price)
        prices = list(state.prices)

        cfg = self.config
        if len(prices) < cfg.min_history:
            logger.debug("%s: warming up (%d/%d prices)", pair, len(prices), cfg.min_history)
            return "HOLD"

        fast = _ema(prices, cfg.fast_ema)
        slow = _ema(prices, cfg.slow_ema)
        rsi = _rsi(prices, cfg.rsi_period)

        # Also compute previous-tick EMAs to detect crossover
        prev_prices = prices[:-1]
        prev_fast = _ema(prev_prices, cfg.fast_ema)
        prev_slow = _ema(prev_prices, cfg.slow_ema)

        signal: Signal = "HOLD"

        if (
            prev_fast <= prev_slow          # was below or equal
            and fast > slow                 # now crossed above → bullish
            and rsi < cfg.rsi_overbought    # not overbought
        ):
            signal = "BUY"

        elif (
            prev_fast >= prev_slow          # was above or equal
            and fast < slow                 # now crossed below → bearish
            and rsi > cfg.rsi_oversold      # not oversold
        ):
            signal = "SELL"

        if signal != state.last_signal or signal != "HOLD":
            logger.info(
                "%s signal=%s | fast_ema=%.4f slow_ema=%.4f rsi=%.1f",
                pair, signal, fast, slow, rsi,
            )

        state.last_signal = signal
        return signal

    def indicators(self, pair: str) -> dict:
        """Return current indicator values for a pair (useful for logging/debugging)."""
        state = self._state(pair)
        prices = list(state.prices)
        cfg = self.config
        if len(prices) < cfg.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}
        return {
            "pair": pair,
            "last_price": prices[-1],
            "fast_ema": round(_ema(prices, cfg.fast_ema), 6),
            "slow_ema": round(_ema(prices, cfg.slow_ema), 6),
            "rsi": round(_rsi(prices, cfg.rsi_period), 2),
            "prices_collected": len(prices),
        }

    def reset(self, pair: str = None):
        """Reset state for one pair, or all pairs if none specified."""
        if pair:
            self._states.pop(pair, None)
        else:
            self._states.clear()

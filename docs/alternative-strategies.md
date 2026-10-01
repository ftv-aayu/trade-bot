# Alternative Strategies

Ready-to-use implementations you can swap in for the momentum strategy.
Each follows the same `update()` / `indicators()` interface.

---

## 1. Bollinger Bands

Prices move in a channel. Buy when price touches the lower band (oversold),
sell when it touches the upper band (overbought).

```
Upper Band = SMA(20) + 2 × StdDev(20)
Middle     = SMA(20)
Lower Band = SMA(20) - 2 × StdDev(20)

BUY:  price <= lower band  (price is cheap relative to recent range)
SELL: price >= upper band  (price is expensive relative to recent range)
```

```python
from collections import deque
from dataclasses import dataclass, field
import math

@dataclass
class BBState:
    prices: deque = field(default_factory=lambda: deque(maxlen=200))
    entry_price: float = 0.0

class BollingerStrategy:
    def __init__(self, period=20, std_multiplier=2.0, min_history=25):
        self.period = period
        self.std_mult = std_multiplier
        self.min_history = min_history
        self._states: dict[str, BBState] = {}

    def _state(self, pair):
        if pair not in self._states:
            self._states[pair] = BBState()
        return self._states[pair]

    def _bands(self, prices):
        n = self.period
        sma = sum(prices[-n:]) / n
        variance = sum((p - sma)**2 for p in prices[-n:]) / n
        std = math.sqrt(variance)
        return sma - self.std_mult * std, sma, sma + self.std_mult * std

    def update(self, pair: str, price: float) -> str:
        state = self._state(pair)
        state.prices.append(price)
        prices = list(state.prices)

        if len(prices) < self.min_history:
            return "HOLD"

        lower, middle, upper = self._bands(prices)
        pnl_pct = ((price - state.entry_price) / state.entry_price * 100) if state.entry_price else 0

        if price <= lower and state.entry_price == 0:
            state.entry_price = price
            return "BUY"
        elif state.entry_price > 0:
            if price >= upper:           # hit upper band → take profit
                state.entry_price = 0.0
                return "SELL"
            elif pnl_pct <= -3.0:        # stop-loss
                state.entry_price = 0.0
                return "SELL"
        return "HOLD"

    def indicators(self, pair: str) -> dict:
        state = self._state(pair)
        prices = list(state.prices)
        if len(prices) < self.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}
        lower, middle, upper = self._bands(prices)
        return {
            "warming_up": False,
            "last_price": prices[-1],
            "bb_lower": round(lower, 6),
            "bb_middle": round(middle, 6),
            "bb_upper": round(upper, 6),
            "entry_price": state.entry_price,
        }
```

**When to use:** Works well in sideways/ranging markets where price bounces
between support and resistance. Performs poorly in strong trends.

---

## 2. MACD (Moving Average Convergence Divergence)

More sophisticated than a simple EMA crossover. Uses three values:
- MACD line = EMA(12) - EMA(26)
- Signal line = EMA(9) of the MACD line
- Histogram = MACD - Signal

```
BUY:  MACD crosses above Signal  (bullish momentum)
SELL: MACD crosses below Signal  (bearish momentum)
```

```python
from collections import deque
from dataclasses import dataclass, field

@dataclass
class MACDState:
    prices: deque = field(default_factory=lambda: deque(maxlen=300))
    macd_history: deque = field(default_factory=lambda: deque(maxlen=50))
    entry_price: float = 0.0

def _ema_series(prices, period):
    """Compute full EMA series (not just last value)."""
    k = 2 / (period + 1)
    emas = [prices[0]]
    for p in prices[1:]:
        emas.append(p * k + emas[-1] * (1 - k))
    return emas

class MACDStrategy:
    def __init__(self, fast=12, slow=26, signal=9, min_history=60):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.min_history = min_history
        self._states: dict[str, MACDState] = {}

    def _state(self, pair):
        if pair not in self._states:
            self._states[pair] = MACDState()
        return self._states[pair]

    def update(self, pair: str, price: float) -> str:
        state = self._state(pair)
        state.prices.append(price)
        prices = list(state.prices)

        if len(prices) < self.min_history:
            return "HOLD"

        fast_emas = _ema_series(prices, self.fast)
        slow_emas = _ema_series(prices, self.slow)
        macd_line = [f - s for f, s in zip(fast_emas, slow_emas)]
        state.macd_history.append(macd_line[-1])

        macd_hist = list(state.macd_history)
        if len(macd_hist) < self.signal + 1:
            return "HOLD"

        signal_line = _ema_series(macd_hist, self.signal)
        prev_macd   = macd_hist[-2]
        prev_signal = signal_line[-2]
        curr_macd   = macd_hist[-1]
        curr_signal = signal_line[-1]

        pnl_pct = ((price - state.entry_price) / state.entry_price * 100) if state.entry_price else 0

        # MACD crossed above signal → BUY
        if prev_macd <= prev_signal and curr_macd > curr_signal and state.entry_price == 0:
            state.entry_price = price
            return "BUY"
        # MACD crossed below signal → SELL (only if profitable)
        elif state.entry_price > 0:
            if pnl_pct <= -2.0:    # stop-loss
                state.entry_price = 0.0
                return "SELL"
            if prev_macd >= prev_signal and curr_macd < curr_signal and pnl_pct >= 0.25:
                state.entry_price = 0.0
                return "SELL"
        return "HOLD"

    def indicators(self, pair: str) -> dict:
        state = self._state(pair)
        if len(state.prices) < self.min_history:
            return {"warming_up": True, "prices_collected": len(state.prices)}
        return {
            "warming_up": False,
            "last_price": list(state.prices)[-1],
            "macd_history_len": len(state.macd_history),
        }
```

**When to use:** Better than simple EMA crossover for trending markets.
Less prone to false signals because it uses three smoothing layers.

---

## 3. Mean Reversion

Based on the idea that prices always return to their average.
Buy when price is abnormally low, sell when it recovers to average.

```
z-score = (current_price - mean) / std_deviation

z < -1.5  →  price is 1.5 std deviations below mean  →  BUY
z > +0.5  →  price recovered to near mean             →  SELL
```

```python
from collections import deque
from dataclasses import dataclass, field
import math

@dataclass
class MRState:
    prices: deque = field(default_factory=lambda: deque(maxlen=200))
    entry_price: float = 0.0

class MeanReversionStrategy:
    def __init__(self, lookback=30, z_buy=-1.5, z_sell=0.5, min_history=35):
        self.lookback = lookback
        self.z_buy = z_buy
        self.z_sell = z_sell
        self.min_history = min_history
        self._states: dict[str, MRState] = {}

    def _state(self, pair):
        if pair not in self._states:
            self._states[pair] = MRState()
        return self._states[pair]

    def _zscore(self, prices):
        n = self.lookback
        window = prices[-n:]
        mean = sum(window) / n
        std = math.sqrt(sum((p - mean)**2 for p in window) / n)
        if std == 0:
            return 0
        return (prices[-1] - mean) / std

    def update(self, pair: str, price: float) -> str:
        state = self._state(pair)
        state.prices.append(price)
        prices = list(state.prices)

        if len(prices) < self.min_history:
            return "HOLD"

        z = self._zscore(prices)
        pnl_pct = ((price - state.entry_price) / state.entry_price * 100) if state.entry_price else 0

        if z <= self.z_buy and state.entry_price == 0:
            state.entry_price = price
            return "BUY"
        elif state.entry_price > 0:
            if pnl_pct <= -3.0:
                state.entry_price = 0.0
                return "SELL"
            if z >= self.z_sell:
                state.entry_price = 0.0
                return "SELL"
        return "HOLD"

    def indicators(self, pair: str) -> dict:
        state = self._state(pair)
        prices = list(state.prices)
        if len(prices) < self.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}
        return {
            "warming_up": False,
            "last_price": prices[-1],
            "zscore": round(self._zscore(prices), 3),
            "entry_price": state.entry_price,
        }
```

**When to use:** Great for stable, non-trending coins that oscillate
around a mean. Does NOT work in strong uptrends or downtrends.

---

## 4. How to Swap In Any Strategy

In `src/main.py`, find these lines:

```python
from src.strategy.momentum import MomentumStrategy, MomentumConfig
strategy = MomentumStrategy(MomentumConfig(...))
```

Replace with whichever strategy you want:

```python
# Option A: Bollinger Bands
from src.strategy.momentum import BollingerStrategy
strategy = BollingerStrategy(period=20, std_multiplier=2.0)

# Option B: MACD
from src.strategy.momentum import MACDStrategy
strategy = MACDStrategy(fast=12, slow=26, signal=9)

# Option C: Mean Reversion
from src.strategy.momentum import MeanReversionStrategy
strategy = MeanReversionStrategy(lookback=30, z_buy=-1.5, z_sell=0.5)
```

Or save them to separate files (`src/strategy/bollinger.py`, etc.)
and import from there.

---

## 5. Comparison Table

| Strategy | Type | Works Best In | Risk |
|---|---|---|---|
| EMA Crossover (current) | Trend-following | Trending markets | False signals in sideways |
| Bollinger Bands | Mean reversion | Ranging markets | Trend breakouts hurt |
| MACD | Trend-following | Trending markets | Slower signals |
| Mean Reversion (z-score) | Mean reversion | Stable coins | Fails in strong trends |

**Rule of thumb:**
- If the market is trending up or down → use trend-following (EMA, MACD)
- If the market is flat/sideways → use mean reversion (Bollinger, z-score)

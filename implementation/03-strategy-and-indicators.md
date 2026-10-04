# 03 — Strategy Engine & Technical Indicators

This document explains the mathematical foundations, signal generation logic, entry/exit rules, and adaptive market regime classification implemented in [`src/strategy/momentum.py`](file:///home/ayush/Practice/trade-bot/src/strategy/momentum.py).

---

## 1. Strategy Philosophy

The strategy is a **Trend-Following Momentum Model with Asymmetric Risk Filters**.

The core premise is straightforward:
- Cryptocurrencies exhibit strong directional momentum over short-to-medium horizons.
- We enter when short-term momentum rises faster than long-term trend, but only when momentum is fresh (not overbought or already exhausted).
- We cut losses immediately (-2.0%) and let winners run (+3.0% or trailing stop).
- We refuse to churn positions at a loss during sideways chop by requiring minimum hold times and minimum profit thresholds on signal exits.

---

## 2. Technical Indicators & Mathematical Formulas

The strategy calculates two primary indicators over a rolling window of closing prices stored in a `collections.deque(maxlen=200)`.

### 2.1 Exponential Moving Average (EMA)

Unlike a Simple Moving Average (SMA) which weights all historical periods equally, the Exponential Moving Average (EMA) applies exponentially decreasing weights to older data points.

#### Mathematical Formula:
$$\text{Multiplier } k = \frac{2}{N + 1}$$
$$\text{EMA}_t = (\text{Price}_t \times k) + (\text{EMA}_{t-1} \times (1 - k))$$

Where $N$ is the period length.

- **Fast EMA ($N=8$)**: $k = 2 / (8 + 1) = 0.2222$ (22.2% weight on latest price).
- **Slow EMA ($N=21$)**: $k = 2 / (21 + 1) = 0.0909$ (9.09% weight on latest price).

#### Implementation in Python:
```python
def _ema(prices: list, period: int) -> float:
    if len(prices) < period:
        return float("nan")
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema
```

---

### 2.2 Relative Strength Index (RSI)

The Relative Strength Index measures the speed and magnitude of recent price changes to evaluate overbought or oversold conditions on a scale of 0 to 100.

#### Mathematical Formula:
$$\text{RS} = \frac{\text{Average Gain over } N \text{ periods}}{\text{Average Loss over } N \text{ periods}}$$
$$\text{RSI} = 100 - \left(\frac{100}{1 + \text{RS}}\right)$$

Where $N = 14$ (default period).

#### Implementation in Python:
```python
def _rsi(prices: list, period: int) -> float:
    if len(prices) < period + 1:
        return 50.0
    relevant = prices[-(period + 1):]
    deltas = [relevant[i+1] - relevant[i] for i in range(len(relevant) - 1)]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
```

---

## 3. Dynamic Market Regime Detection

The bot classifies the broader market into 5 regimes in `src/main.py:_detect_market_state()` by analyzing all warmed pairs simultaneously:

```
                  ┌─────────────────────────────────────┐
                  │ Calculate Market Breadth Metrics:   │
                  │ - Average RSI across all pairs      │
                  │ - % of pairs with Fast EMA > Slow   │
                  │ - % of pairs with 24h Change > 0    │
                  └──────────────────┬──────────────────┘
                                     │
           ┌─────────────────────────┼─────────────────────────┐
           ▼                         ▼                         ▼
   [Avg RSI < 38 &           [42 <= Avg RSI <= 62       [Avg RSI > 65 &
    Rising % > 40%]           & EMA Up % > 45%]          EMA Up % > 60%]
           │                         │                         │
           ▼                         ▼                         ▼
     **RECOVERY**              **TRENDING**              **OVERBOUGHT**
 (Looser RSI 38-62,        (Standard RSI 44-60,      (Tight RSI 48-56,
  Sep 0.02%, Target 2.5%)   Sep 0.04%, Target 3.5%)   Target 2.0%, Quick Exit)
```

### Regime Parameter Sets:

| Regime | Market Condition | RSI Buy Zone | Min EMA Sep | Stop Loss | Take Profit | Confirmation |
|---|---|---|---|---|---|---|
| **RECOVERY** | Oversold bounce | 38.0 – 62.0 | 0.02% | 1.5% | 2.5% | 2 ticks |
| **TRENDING** | Healthy sustained uptrend | 44.0 – 60.0 | 0.04% | 2.0% | 3.5% | 2 ticks |
| **RANGING** | Choppy / Sideways | 45.0 – 56.0 | 0.06% | 1.5% | 2.0% | 3 ticks |
| **OVERBOUGHT** | Market rally overextended | 48.0 – 56.0 | 0.08% | 1.5% | 2.0% | 3 ticks |
| **DOWNTURN** | Broad selling pressure | 50.0 – 58.0 | 0.08% | 1.5% | 2.0% | 3 ticks |

---

## 4. Buy Signal Rules (Entry Logic)

For `strategy.update(pair, price)` to return `"BUY"`, **ALL** of the following conditions must be satisfied:

1. **Warmup History Available**:
   `len(prices) >= min_history` (at least 50 price ticks collected).
2. **Fresh Crossover or Confirmed Crossover Window**:
   Either:
   - Fresh bullish crossover: `prev_fast <= prev_slow` and `fast > slow`, OR
   - Trend confirmation window: `confirm_ticks <= ticks_above_slow <= confirm_ticks + 2` (ensures we only enter within 2 ticks of a verified crossover, avoiding stale trends).
3. **RSI in Healthy Momentum Zone**:
   `rsi_buy_min <= rsi <= rsi_buy_max` (default: `45.0 <= RSI <= 58.0`).
   - Prevents buying into exhausted overbought spikes (`RSI > 58`).
   - Prevents catching falling knives in downtrends (`RSI < 45`).
4. **Meaningful EMA Separation**:
   $$\text{Separation \%} = \frac{\text{Fast EMA} - \text{Slow EMA}}{\text{Slow EMA}} \times 100 \ge 0.05\%$$
   Filters out flat micro-crossovers caused by market noise.
5. **No Active Post-Loss Cooldown**:
   `cooldown_cycles == 0` (asset is not in a lockout period from a previous loss).
6. **No Open Position on This Pair**:
   `entry_price == 0` and `last_signal != "BUY"`.

---

## 5. Multi-Tier Sell Rules (Exit Logic)

Once a position is entered, the bot evaluates exits across 5 distinct protective tiers:

```
[Holding Position]
   │
   ├─► 1. Trailing Stop Elevation:
   │      If Unrealized Gain >= +1.5%, move reference entry_price to breakeven (entry * 1.002).
   │
   ├─► 2. Hard Stop-Loss (Priority 1):
   │      If Price <= entry_price * (1 - stop_loss_pct) [e.g. -2.0%]:
   │      ──► Emit SELL immediately. Set 3-cycle cooldown lockout.
   │
   ├─► 3. Take-Profit Target (Priority 2):
   │      If Price >= entry_price * (1 + take_profit_pct) [e.g. +3.0%]:
   │      ──► Emit SELL immediately. Lock in profit.
   │
   ├─► 4. Bearish EMA Crossover Exit (Priority 3):
   │      If Fast EMA crosses below Slow EMA AND RSI > 45 AND hold_cycles >= 3 AND PnL >= +0.25%:
   │      ──► Emit SELL. (Never exits at a loss on signal alone).
   │
   └─► 5. Proactive Loser Exit (Main loop level):
          If PnL <= -0.8%, Fast < Slow, RSI < 40, and hold_cycles >= 3:
          ──► Force SELL to free capital before reaching hard stop.
```

---

## 6. Cooldown Lockout Protection

When a trade closes at a loss (via hard stop-loss or proactive exit), `strategy.notify_sold(pair, was_loss=True)` sets:
$$\text{state.cooldown\_cycles} = 3$$

Because the main loop polls every 5 minutes, a 3-cycle lockout forces the bot to ignore all buy signals on that specific coin for **15 minutes**. This prevents "revenge trading" or re-entering a crashing asset repeatedly during rapid selloffs.

# Trading Strategy Documentation

This document explains every concept, indicator, and decision rule used in this bot.
No prior trading knowledge assumed.

---

## Table of Contents

1. [How the Bot Makes Decisions](#1-how-the-bot-makes-decisions)
2. [What is Price Data and How It's Stored](#2-what-is-price-data-and-how-its-stored)
3. [Indicator 1 — Exponential Moving Average (EMA)](#3-indicator-1--exponential-moving-average-ema)
4. [Indicator 2 — Relative Strength Index (RSI)](#4-indicator-2--relative-strength-index-rsi)
5. [The EMA Crossover Strategy](#5-the-ema-crossover-strategy)
6. [All Buy Conditions (AND logic)](#6-all-buy-conditions-and-logic)
7. [All Sell Conditions](#7-all-sell-conditions)
8. [Risk Management Rules](#8-risk-management-rules)
9. [Warmup Period and Why It Exists](#9-warmup-period-and-why-it-exists)
10. [Database Schema — How Data Is Stored](#10-database-schema--how-data-is-stored)
11. [Full Decision Flow Diagram](#11-full-decision-flow-diagram)
12. [How to Add a New Strategy](#12-how-to-add-a-new-strategy)

---

## 1. How the Bot Makes Decisions

Every 5 minutes the bot:

1. Fetches live prices for all 88 trading pairs from Roostoo
2. Saves every price to the SQLite database
3. Feeds each new price into the strategy engine
4. The strategy returns one of three signals: **BUY**, **SELL**, or **HOLD**
5. If BUY and we have no position → place a buy order
6. If SELL and we hold the coin → place a sell order
7. If HOLD → do nothing, wait for next cycle

The strategy never looks at news, volume trends, or external data.
It only looks at **past prices** to compute two indicators: EMA and RSI.

---

## 2. What is Price Data and How It's Stored

Every 5 minutes, the bot fetches this for each pair:

```
BTC/USD at 10:00:
  LastPrice  = 84,000.00   ← the actual last trade price
  MaxBid     = 83,999.50   ← highest price a buyer is willing to pay
  MinAsk     = 84,000.50   ← lowest price a seller is willing to accept
  Change24h  = +0.012      ← 1.2% rise over the last 24 hours
```

This is saved to the `prices` table in `data/bot.sqlite3`:

```
timestamp_ms  | pair    | price    | bid      | ask      | change_24h | spread_bps
--------------+---------+----------+----------+----------+------------+-----------
1790800000000 | BTC/USD | 84000.00 | 83999.50 | 84000.50 | 0.012      | 0.595
1790800300000 | BTC/USD | 84050.00 | 84049.50 | 84050.50 | 0.018      | 0.595
```

`spread_bps` = how wide the gap between buy and sell price is, in basis points.
Formula: `(ask - bid) / price × 10000`

A spread of 0.595 bps means the gap is 0.00595% of the price — very tight, healthy.

---

## 3. Indicator 1 — Exponential Moving Average (EMA)

### What is a Moving Average?

A moving average smooths out price noise by averaging prices over a window.

**Simple Moving Average (SMA)** — equal weight to all prices:
```
SMA(5) at tick 5 = (P1 + P2 + P3 + P4 + P5) / 5
```

**Exponential Moving Average (EMA)** — more weight to recent prices:
```
EMA today = Price today × k  +  EMA yesterday × (1 - k)
where k = 2 / (period + 1)
```

For EMA(8):  k = 2/9 = 0.222  → today's price gets 22.2% weight
For EMA(21): k = 2/22 = 0.091 → today's price gets 9.1% weight

### Why Two EMAs?

The bot uses two:
- **Fast EMA (period=8)** — reacts quickly to price changes
- **Slow EMA (period=21)** — reacts slowly, represents the longer trend

```
Price:    84,000  84,100  84,200  84,300  84,500  84,800
Fast EMA:   ...   84,050  84,122  84,195  84,325  84,550   ← moves fast
Slow EMA:   ...   84,010  84,030  84,060  84,120  84,230   ← moves slow
```

When price rises, the Fast EMA rises faster than the Slow EMA.

### Code Implementation

```python
def _ema(prices: list, period: int) -> float:
    k = 2 / (period + 1)
    ema = prices[0]           # start from first price
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)   # each tick: blend new price in
    return ema
```

---

## 4. Indicator 2 — Relative Strength Index (RSI)

### What is RSI?

RSI measures how fast and how much prices have been rising vs falling
over the last N periods (default: 14). Output is always between 0 and 100.

```
RSI = 100 - (100 / (1 + RS))
where RS = Average Gain / Average Loss over last 14 ticks
```

### Interpretation

```
RSI > 70  →  Overbought — price rose too fast, likely to pull back
RSI < 30  →  Oversold   — price fell too fast, likely to bounce
RSI 40–60 →  Neutral    — healthy momentum zone
```

### Example

If BTC rose 10 of the last 14 ticks and fell 4:
```
Avg gain = (sum of gains) / 14
Avg loss = (sum of losses) / 14
RS = avg_gain / avg_loss
RSI = 100 - 100/(1 + RS)
```

### Code Implementation

```python
def _rsi(prices: list, period: int) -> float:
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0              # all gains, no losses
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
```

---

## 5. The EMA Crossover Strategy

### Core Concept

A **crossover** happens when the fast EMA crosses the slow EMA:

```
BULLISH CROSSOVER (BUY signal):
  Previous tick:  fast_ema <= slow_ema   (fast was below or equal)
  Current tick:   fast_ema >  slow_ema   (fast is now above)
  Meaning: short-term momentum just turned upward

BEARISH CROSSOVER (SELL signal):
  Previous tick:  fast_ema >= slow_ema   (fast was above or equal)
  Current tick:   fast_ema <  slow_ema   (fast is now below)
  Meaning: short-term momentum just turned downward
```

### Visual Example

```
Price:    100  101  102  103  102  101  100  99   98   97
Fast EMA:  100  100.2 100.6 101.1 101.2 101.0 100.6 100.0 99.3 98.5
Slow EMA:  100  100.1 100.3 100.6 100.7 100.7 100.6 100.4 100.1 99.7

Tick 4: fast(101.1) > slow(100.6) → BULLISH CROSSOVER → BUY
Tick 8: fast(100.0) < slow(100.4) → BEARISH CROSSOVER → SELL

But we also need: RSI between 40–60, EMA separation ≥ 0.02%
```

### Why Crossover Alone Is Not Enough

Pure EMA crossover has two problems:
1. **False signals** — tiny price wiggles cause micro-crossovers
2. **Overbought entries** — buying after a big rally (RSI > 60) means catching the top

That's why we add RSI and EMA separation filters.

---

## 6. All Buy Conditions (AND logic)

**ALL of the following must be true simultaneously for a BUY signal:**

```
Condition 1: EMA Crossover
  prev_fast <= prev_slow   AND   fast > slow
  The fast EMA just crossed above the slow EMA this tick.

Condition 2: Trend Confirmation
  fast > slow
  Double-check that fast is genuinely above slow (not a floating point tie).

Condition 3: RSI in Healthy Zone
  40.0 <= RSI <= 60.0
  - RSI > 60 = overbought, don't chase the rally
  - RSI < 40 = already in downtrend, don't buy a falling knife

Condition 4: EMA Separation Meaningful
  (fast - slow) / slow × 100 >= 0.02%
  The gap must be real, not just noise from tiny price moves.

Condition 5: Not Just Restored
  just_restored == False
  Skip the very first live tick after a DB reload to avoid phantom crossovers.
```

**In code:**
```python
crossed_up = prev_fast <= prev_slow and fast > slow
if (
    crossed_up
    and fast > slow
    and 40.0 <= rsi <= 60.0
    and separation_pct >= 0.02
    and not state.just_restored
):
    signal = "BUY"
```

---

## 7. All Sell Conditions

Once we hold a position (after a BUY), the bot monitors three exit rules
on every tick. They are checked in priority order:

### Exit 1 — Hard Stop-Loss (highest priority)

```
Trigger:  current_price < entry_price × (1 - 0.02)
          i.e., position is down more than 2%

Action:   SELL immediately, no other conditions checked
Reason:   Limit the damage. A 2% loss is recoverable.
          A 10% loss is not.

Example:  Bought BTC at $84,000
          Current price: $82,300  (down 2.02%)
          → SELL immediately
```

### Exit 2 — Take Profit (second priority)

```
Trigger:  current_price > entry_price × (1 + 0.03)
          i.e., position is up more than 3%

Action:   SELL immediately, lock in the gain
Reason:   Don't get greedy. A 3% gain minus 0.2% commission = 2.8% net profit.

Example:  Bought BTC at $84,000
          Current price: $86,600  (up 3.09%)
          → SELL immediately
```

### Exit 3 — EMA Signal Sell (lowest priority, most restrictive)

```
Trigger (ALL must be true):
  a. prev_fast >= prev_slow AND fast < slow   ← bearish crossover
  b. RSI > 45                                  ← not oversold (could bounce)
  c. hold_cycles >= 3                          ← held for at least 15 minutes
  d. pnl_pct >= 0.25%                         ← must be in profit to sell on signal

Reason:   Only exit on signal if we're actually profitable.
          Never sell at a loss just because EMA crossed.

Example:  Bought at $84,000, now at $84,300 (+0.36%)
          EMA just crossed down, RSI=52, held 4 cycles
          → SELL (profitable signal exit)

Counter-example: Bought at $84,000, now at $83,900 (-0.12%)
          EMA crosses down
          → HOLD (not profitable enough, wait for recovery or stop-loss)
```

### Why This Prevents the Losses We Had Before

Old behavior:
```
Bought NEAR at $5.115
5 minutes later EMA crossed down
Sold at $5.103 → loss of $17 + $14 commission = -$31 total
```

New behavior:
```
Bought NEAR at $5.115
EMA crosses down at $5.103 (pnl = -0.23%)
→ HOLD (not profitable, wait)
Price recovers to $5.145 (pnl = +0.59%)
EMA crosses down again
→ SELL (profitable signal exit, +$21 net)
```

---

## 8. Risk Management Rules

These rules live in `src/risk/manager.py` and sit on top of the strategy.

### Position Sizing

```
Max per position = 15% of total portfolio

Example: $50,000 portfolio
  Max position = $7,500 per coin
  This limits exposure — no single coin can wreck the portfolio

Code:
  size_usd = min(portfolio × 0.15, available_usd × 0.95)
```

### Cash Reserve

```
Always keep 20% of starting balance as cash ($10,000 on a $50k account).
Never invest below this threshold.

Reason: Keeps liquidity for better opportunities and covers emergencies.

Code:
  MIN_CASH_RESERVE = STARTING_BALANCE × 0.20
  if usd_free < MIN_CASH_RESERVE: skip this buy
```

### Portfolio Drawdown Halt

```
If portfolio drops more than 12% from its peak value:
  → Stop all trading, monitor only

Reason: Something is systematically wrong. Don't dig the hole deeper.

Example: Portfolio peaks at $52,000
         If it drops to $45,760 (12% below peak) → halt
```

### Commission Awareness

```
Every trade costs:
  MARKET order (BUY/SELL):  0.1% of trade value
  LIMIT order:               0.05% of trade value

Round-trip cost = 0.2% (buy + sell)
So minimum profit needed to break even = 0.2%
That's why min_profit_pct = 0.25% (slightly above breakeven)
```

---

## 9. Warmup Period and Why It Exists

The EMA and RSI calculations need historical prices to be accurate.

```
EMA(8)  needs at least 8  prices to compute
EMA(21) needs at least 21 prices to compute
RSI(14) needs at least 15 prices to compute
min_history = 50 prices for reliable signals
```

With 5-minute polling:
```
50 ticks × 5 minutes = 250 minutes = ~4 hours warmup
```

### Skipping Warmup with the Database

The bot stores every price tick in `data/bot.sqlite3`.
On restart, it loads the last 200 prices per pair from the DB:

```
Startup sequence:
1. Load last 200 prices per pair from DB
2. Feed them into the strategy (fast, no API calls)
3. Reset all position state (clean slate, no ghost positions)
4. Apply just_restored = True flag (skip first live tick to avoid phantom signals)
5. Fetch one fresh live price (correct any staleness drift)
6. Start trading immediately if ≥ 50 ticks restored
```

### Staleness Correction

If the DB data is more than 10 minutes old (e.g., bot was off for hours):
- The EMA was built on stale prices
- One fresh live price is fed after restore to pull the EMA toward current market
- The `just_restored` flag blocks the first signal to prevent a phantom crossover

---

## 10. Database Schema — How Data Is Stored

Location: `data/bot.sqlite3`

### prices table

Stores every price snapshot from every poll cycle.

```sql
CREATE TABLE prices (
    timestamp_ms    INTEGER,   -- Unix timestamp in milliseconds (13 digits)
    pair            TEXT,      -- e.g. 'BTC/USD'
    price           REAL,      -- LastPrice from ticker
    bid             REAL,      -- MaxBid (best buy offer)
    ask             REAL,      -- MinAsk (best sell offer)
    change_24h      REAL,      -- 24h price change as decimal (0.012 = +1.2%)
    unit_trade_value REAL,     -- USD trading volume
    spread_bps      REAL,      -- (ask-bid)/price × 10000
    PRIMARY KEY (timestamp_ms, pair)
);
```

**How it's written:** Every cycle, all 86+ ticker prices are bulk-inserted.
**How it's read:** `_restore_warmup()` queries last 200 rows per pair for strategy init.

### trades table

Every order placed (paper or live).

```sql
CREATE TABLE trades (
    id           INTEGER PRIMARY KEY,
    timestamp_ms INTEGER,   -- when the order filled
    pair         TEXT,      -- 'BTC/USD'
    side         TEXT,      -- 'BUY' or 'SELL'
    quantity     REAL,      -- how many coins
    price        REAL,      -- fill price
    notional     REAL,      -- quantity × price (USD value)
    fee          REAL,      -- commission charged
    mode         TEXT,      -- 'paper' or 'live'
    order_id     TEXT,      -- exchange order ID
    status       TEXT       -- 'FILLED', 'PENDING', etc.
);
```

### equity table

Portfolio value snapshot every cycle.

```sql
CREATE TABLE equity (
    timestamp_ms INTEGER PRIMARY KEY,
    equity       REAL    -- total portfolio value in USD
);
```

Used to compute Sharpe, Sortino, Calmar ratios and plot performance over time.

### api_events table

Logs every API call success/failure.

```sql
CREATE TABLE api_events (
    id           INTEGER PRIMARY KEY,
    timestamp_ms INTEGER,
    endpoint     TEXT,     -- '/v3/ticker', '/v3/place_order', etc.
    success      INTEGER,  -- 1 = success, 0 = failure
    message      TEXT      -- details or error message
);
```

### state table

Key-value store for bot configuration and session info.

```sql
CREATE TABLE state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- Contains: mode, start_balance, started_at, stopped_at
```

---

## 11. Full Decision Flow Diagram

```
Every 5 minutes:
│
├─ Fetch live prices (all 86 pairs)
│   └─ Save to prices table in DB
│
├─ Update portfolio value
│   └─ Save to equity table in DB
│
├─ For each pair:
│   │
│   ├─ Feed new price into strategy
│   │   ├─ prices.append(new_price)
│   │   ├─ Compute fast_ema = EMA(prices, 8)
│   │   ├─ Compute slow_ema = EMA(prices, 21)
│   │   └─ Compute rsi      = RSI(prices, 14)
│   │
│   ├─ Are we holding this coin?
│   │   │
│   │   ├─ YES → Check exit conditions:
│   │   │   ├─ pnl < -2%?     → SELL (stop-loss)
│   │   │   ├─ pnl > +3%?     → SELL (take-profit)
│   │   │   ├─ EMA crossed ▼  AND
│   │   │   │  RSI > 45       AND
│   │   │   │  held ≥ 3 cycles AND
│   │   │   │  pnl ≥ +0.25%?  → SELL (signal exit)
│   │   │   └─ None of above   → HOLD
│   │   │
│   │   └─ NO → Check entry conditions:
│   │       ├─ EMA crossed ▲?          NO  → HOLD
│   │       ├─ 40 ≤ RSI ≤ 60?          NO  → HOLD
│   │       ├─ EMA separation ≥ 0.02%? NO  → HOLD
│   │       ├─ usd_free > $10,000?     NO  → HOLD (reserve)
│   │       └─ All YES                 → BUY
│   │
│   └─ Execute signal:
│       ├─ BUY  → place_order(pair, BUY,  qty)  → save to trades DB
│       ├─ SELL → place_order(pair, SELL, qty)  → save to trades DB
│       └─ HOLD → nothing
│
└─ Sleep 5 minutes, repeat
```

---

## 12. How to Add a New Strategy

The bot is built so you can drop in any strategy as long as it follows
this interface:

```python
class MyStrategy:
    def update(self, pair: str, price: float) -> Literal["BUY", "SELL", "HOLD"]:
        # Your logic here
        # Called every poll cycle with the latest price
        return "HOLD"

    def indicators(self, pair: str) -> dict:
        # Return current indicator values for display
        # Must include 'warming_up': True/False
        return {"warming_up": False, "last_price": 0.0}
```

Then in `src/main.py`, replace:

```python
from src.strategy.momentum import MomentumStrategy, MomentumConfig
strategy = MomentumStrategy(MomentumConfig(...))
```

With:

```python
from src.strategy.my_strategy import MyStrategy
strategy = MyStrategy()
```

Nothing else needs to change.

### Strategy Ideas to Explore

| Strategy | What It Does | Best Market |
|---|---|---|
| Mean Reversion | Buy when price is far below its average | Sideways/ranging |
| Bollinger Bands | Buy at lower band, sell at upper band | Ranging |
| MACD | Two EMAs + signal line, smoother than pure crossover | Trending |
| Volume Weighted | Weight signals by trading volume | All |
| ML Model | Train a model on historical data, output BUY/SELL/HOLD | All |

See `docs/alternative-strategies.md` for implementation examples.

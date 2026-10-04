# 01 — Architecture & Execution Flow

This document details the high-level architecture of the trading bot, the lifecycle of a single polling cycle, and the exact flow of data through all subsystems.

---

## 1. System Architecture

The trading bot is organized into loosely coupled, highly focused modules. The main coordinator is `src/main.py`, which integrates the API client, local simulation engine, persistence layer, quantitative strategy, risk engine, and logger.

```
                  ┌──────────────────────────────────────────────┐
                  │                 src/main.py                  │
                  │             (Main Loop & Engine)             │
                  └──────┬─────────────┬─────────────┬───────────┘
                         │             │             │
        ┌────────────────┘             │             └────────────────┐
        ▼                              ▼                              ▼
┌──────────────┐              ┌────────────────┐             ┌────────────────┐
│src/api/      │              │src/strategy/   │             │src/risk/       │
│client.py     │              │momentum.py     │             │manager.py      │
│(Roostoo REST)│              │(EMA/RSI Signals│             │(Kelly Sizing & │
└──────┬───────┘              └────────┬───────┘             │Drawdown Halt)  │
       │                               │                     └────────────────┘
       │                               │
       │              ┌────────────────┴────────────────┐
       ▼              ▼                                 ▼
┌──────────────┐ ┌──────────────┐                ┌──────────────┐
│src/paper_    │ │src/db.py     │                │src/logger/   │
│trader.py     │ │(SQLite WAL   │                │trade_logger  │
│(Simulation)  │ │bot.sqlite3)  │                │(CSV & JSONL) │
└──────────────┘ └──────────────┘                └──────────────┘
```

### Module Responsibilities:

| Module | Primary File | Key Responsibility |
|---|---|---|
| **Main Engine** | [`src/main.py`](file:///home/ayush/Practice/trade-bot/src/main.py) | Orchestrates startup, periodic polling, market regime detection, signal dispatching, position rotation, order routing, and shutdown. |
| **Exchange Client** | [`src/api/client.py`](file:///home/ayush/Practice/trade-bot/src/api/client.py) | Manages HTTP communication with the Roostoo mock exchange, handles HMAC-SHA256 request signing, and provides typed helper methods. |
| **Strategy Engine** | [`src/strategy/momentum.py`](file:///home/ayush/Practice/trade-bot/src/strategy/momentum.py) | Ingests rolling price streams, computes Fast/Slow EMAs and RSI, tracks trade confirmation ticks, detects crossovers, and generates `BUY`/`SELL`/`HOLD` signals. |
| **Risk Manager** | [`src/risk/manager.py`](file:///home/ayush/Practice/trade-bot/src/risk/manager.py) | Calculates position sizes using Half-Kelly criterion adjusted for volatility and signal conviction; monitors high-water mark equity and triggers drawdown circuit breaker. |
| **Paper Trader** | [`src/paper_trader.py`](file:///home/ayush/Practice/trade-bot/src/paper_trader.py) | Simulates order execution, fee deduction, position averaging, and wallet tracking locally without sending requests to the exchange. |
| **Database Layer** | [`src/db.py`](file:///home/ayush/Practice/trade-bot/src/db.py) | Embedded SQLite interface using Write-Ahead Logging (WAL) and thread-local connections to store prices, orders, equity curves, and API telemetry. |
| **Trade Logger** | [`src/logger/trade_logger.py`](file:///home/ayush/Practice/trade-bot/src/logger/trade_logger.py) | Appends executed trades to `logs/trades.csv`, logs equity snapshots to `logs/performance.jsonl`, and computes Sharpe, Sortino, and Calmar ratios. |

---

## 2. The Startup Sequence

When `python src/main.py` is executed, the bot performs the following initialization steps before entering the infinite loop:

```
[Start]
   │
   ├─► 1. Initialize TeeStream & Logging (mirrors console to logs/bot.log in IST time)
   │
   ├─► 2. Instantiate RoostooClient & Verify Connectivity (GET /v3/serverTime)
   │
   ├─► 3. Load Exchange Metadata (GET /v3/exchangeInfo)
   │      - Extract all tradable pairs (CanTrade == True)
   │      - Cache AmountPrecision and MiniOrder requirements
   │
   ├─► 4. Initialize Balance & Mode
   │      - If PAPER_MODE: Initialize PaperTrader with STARTING_BALANCE ($50,000)
   │      - If LIVE_MODE: Query real SpotWallet balance and auto-set STARTING_BALANCE
   │
   ├─► 5. Initialize Strategy, Risk Manager, Trade Logger, and Database (BotDB)
   │      - Record session start in SQLite state table
   │
   ├─► 6. Restore Warmup History from SQLite (_restore_warmup)
   │      - Query up to 200 historical prices per pair from SQLite
   │      - Feed prices into MomentumStrategy while suppressing stop-loss triggers
   │      - Fetch one live ticker snapshot to apply drift correction for stale data (>10 min old)
   │      - Set just_restored = True on all pairs to prevent false crossover orders
   │
   ├─► 7. Reconcile Existing Holdings (Live mode)
   │      - Inspect SpotWallet for non-USD coins
   │      - Register existing positions into MomentumStrategy with notify_bought()
   │      - Ensures stop-loss and take-profit immediately protect pre-existing assets
   │
   ▼
[Enter Polling Loop]
```

---

## 3. The 5-Minute Polling Cycle (Step-by-Step)

The main loop runs on a configured interval (default: `POLL_INTERVAL = 300` seconds / 5 minutes). Each iteration executes the following sequence:

### Step 1: Ingest Live Market Tickers
- Calls `client.get_all_tickers()` to fetch the latest price book for all 80+ pairs.
- If the ticker response is empty or errors out, logs an API event to SQLite, waits `POLL_INTERVAL`, and retries.
- Bulk-inserts all ticker rows (price, bid, ask, 24h change, unit volume, spread in basis points) into the SQLite `prices` table.

### Step 2: Compute Portfolio Value & Check Auto-Stop Circuit Breakers
- Computes total current equity = `Free USD + Locked USD + Sum(Coin_Quantity * LastPrice)`.
- Updates `RiskManager.update_balance(portfolio_val)`.
- Appends equity snapshot to `TradeLogger` and SQLite `equity` table.
- **Profit Target Check**: If `portfolio_pnl_pct >= PROFIT_TARGET_PCT`, executes `shutdown()` and exits.
- **Stop Loss Check**: If `portfolio_pnl_pct <= -STOP_LOSS_PCT`, executes `shutdown()` and exits.
- **Drawdown Circuit Breaker**: If `RiskManager.is_halted()` is True (drawdown > 12% from peak), logs a warning, skips all trade logic, and sleeps until the next cycle.

### Step 3: Adaptive Market Regime Detection
- When ≥80% of pairs have completed warmup, the bot analyzes market-wide breadth:
  - Average RSI across all pairs.
  - Percentage of pairs where Fast EMA > Slow EMA (`ema_up_pct`).
  - Percentage of pairs with positive 24-hour price change (`rising_pct`).
- Classifies the market into one of five states:
  - `RECOVERY` (oversold bounce)
  - `TRENDING` (healthy uptrend)
  - `RANGING` (choppy, sideways)
  - `OVERBOUGHT` (extended rally, high reversal risk)
  - `DOWNTURN` (broad selling pressure)
- Dynamically adjusts `MomentumStrategy` parameters (RSI entry bounds, EMA separation threshold, confirmation ticks, stop-loss and take-profit targets) according to market conditions.

### Step 4: Pair-by-Pair Strategy Evaluation
Iterates over every pair in `TRADE_PAIRS`:
1. Feeds the latest price into `strategy.update(pair, price)`.
2. Inspects indicators: `fast_ema`, `slow_ema`, `rsi`, `ticks_above_slow`, `cooldown_cycles`.
3. Checks current coin balance (`coin_held`). Note: In live mode, balance check uses `Free + Lock` to prevent duplicate buy orders when an existing limit order is pending.

### Step 5: Exit & Trailing Stop Evaluation (If holding the coin)
1. **Trailing Stop Adjustment**: If current unrealized profit is `≥ 1.5%`, the bot raises the position's reference `entry_price` to `entry_price * 1.002` (breakeven including round-trip 0.20% commission). This ensures that any subsequent pullback triggers a stop-loss at profit or breakeven, not at a loss.
2. **Proactive Loser Exit**: If the position is losing between `-0.8%` and `-2.0%`, Fast EMA has crossed below Slow EMA, RSI has dropped `< 40`, and the position has been held for `≥ 3 cycles` (15 min), the bot immediately executes a proactive market sell without waiting for the hard -2.0% stop-loss.
3. **Signal Sell Execution**: If the strategy returns `SELL` (due to hard stop-loss `-2.0%`, take-profit `+3.0%`, or a profitable bearish EMA crossover with `pnl ≥ +0.25%`):
   - Places a `SELL` market order for all `Free` coins.
   - Logs the trade in `logs/trades.csv` and SQLite `trades` table.
   - Notifies the strategy via `strategy.notify_sold(pair, was_loss)`. If closed at a loss, enters a 3-cycle cooldown lockout for that pair.

### Step 6: Entry & Rotation Evaluation (If not holding the coin)
If the strategy returns `BUY`:
1. **Position Cap & Rotation Check**:
   - Counts currently open positions.
   - If `open_positions >= MAX_POSITIONS` (default: 6):
     - Scans open positions for a "loser candidate" (unrealized P&L between `-0.5%` and `-1.8%`).
     - Scores the new candidate signal strength (`new_score = ema_sep * 10 + (1 - |rsi - 52| / 10)`).
     - If a loser exists and the new signal score is strong (`> 0.5`), sells the losing position to free up capital and position slots (**Proactive Rotation**).
     - Otherwise, skips the buy signal.
2. **Cash Reserve Guard**:
   - Verifies `usd_free >= STARTING_BALANCE * 0.20` ($10,000 reserve on $50k account). If available cash is below reserve, the buy is skipped.
3. **Position Sizing**:
   - `RiskManager.position_size_usd()` computes allocation using Half-Kelly scaling based on signal conviction and 24h volatility.
   - Rounds down to the exchange's `AmountPrecision`.
   - Verifies minimum order value (`qty * price >= MiniOrder` and `qty * price >= $1,000` to prevent fee erosion).
4. **Order Execution**:
   - Executes market buy order via `PaperTrader` or `RoostooClient`.
   - Logs trade details to CSV and SQLite.
   - Calls `strategy.notify_bought(pair, filled_price)` to begin tracking entry price and hold cycles.
   - Re-queries the live exchange wallet to obtain the exact updated USD free balance.

### Step 7: Periodic Performance Summary & Sleep
- Prints cycle summary and paper positions table (if paper mode).
- Every `PERF_INTERVAL` (default: 15 minutes / 900 seconds), computes and prints:
  - Total Return %
  - Sharpe Ratio
  - Sortino Ratio
  - Calmar Ratio
  - Max Drawdown %
  - SQLite row statistics (`prices`, `trades`, `equity`)
- Sleeps for `POLL_INTERVAL` (300 seconds) before repeating.

---

## 4. Graceful Shutdown & Recovery

When a user presses `Ctrl+C` (`SIGINT`) or an auto-stop condition is reached:
1. `KeyboardInterrupt` is caught in `src/main.py`.
2. `shutdown()` is invoked:
   - Evaluates final portfolio value across cash and open assets.
   - **Crucial Design Choice**: Open positions are **NOT** panic-sold on shutdown. They remain intact so that if the bot is restarted, positions resume tracking seamlessly without incurring unnecessary market spread and taker fees.
   - Computes final session metrics and prints the closing summary.
   - Updates the SQLite `state` table with `stopped_at` timestamp.
   - Flushes all stdout and log buffers cleanly.

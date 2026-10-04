# 05 — Data Persistence & Database Architecture

This document describes the embedded database layer, schema design, Write-Ahead Logging (WAL) concurrency, fast warmup restoration from disk, and audit SQL queries implemented in [`src/db.py`](file:///home/ayush/Practice/trade-bot/src/db.py).

---

## 1. Database Architecture & Concurrency

The trading bot uses an embedded **SQLite 3** database located at `data/bot.sqlite3`.

### Key Architectural Properties:
1. **Write-Ahead Logging (WAL Mode)**:
   - Configured via `PRAGMA journal_mode=WAL`.
   - Allows concurrent read queries (such as terminal monitors or analytics scripts) while the bot writes price updates without locking the database.
2. **Synchronous Mode (`PRAGMA synchronous=NORMAL`)**:
   - Provides safe persistence on Linux with minimal I/O latency.
3. **Thread-Local Connection Management**:
   - `BotDB` uses Python's `threading.local()` to ensure every thread maintains its own isolated `sqlite3.Connection`, preventing race conditions.
4. **Automatic Migration**:
   - Schema upgrades (e.g., adding `session_id` to the `trades` table) are automatically verified and executed on database initialization.

---

## 2. Full Database Schema

```sql
-- Bot session configuration and runtime metadata
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Real-time price and order-book snapshots (inserted every 5-min cycle)
CREATE TABLE IF NOT EXISTS prices (
    timestamp_ms     INTEGER NOT NULL,  -- 13-digit Unix millisecond timestamp
    pair             TEXT NOT NULL,     -- e.g., 'BTC/USD'
    price            REAL NOT NULL,     -- LastPrice from ticker
    bid              REAL NOT NULL,     -- MaxBid
    ask              REAL NOT NULL,     -- MinAsk
    change_24h       REAL NOT NULL,     -- 24h change decimal (0.012 = +1.2%)
    unit_trade_value REAL NOT NULL,     -- USD trading volume
    spread_bps       REAL NOT NULL,     -- Bid-ask spread in basis points ((ask-bid)/price * 10000)
    PRIMARY KEY(timestamp_ms, pair)
);
CREATE INDEX IF NOT EXISTS idx_prices_pair_ts ON prices(pair, timestamp_ms);

-- Executed orders and filled trades (Paper and Live)
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    pair         TEXT NOT NULL,
    side         TEXT NOT NULL,         -- 'BUY' or 'SELL'
    quantity     REAL NOT NULL,
    price        REAL NOT NULL,         -- Filled average price
    notional     REAL NOT NULL,         -- quantity * price
    fee          REAL NOT NULL,         -- Commission charged in USD
    mode         TEXT NOT NULL,         -- 'paper' or 'live'
    order_id     TEXT,
    status       TEXT NOT NULL,         -- 'FILLED', 'OPEN', etc.
    session_id   TEXT                   -- Unique run identifier
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp_ms);

-- Portfolio equity curve snapshots
CREATE TABLE IF NOT EXISTS equity (
    timestamp_ms INTEGER PRIMARY KEY,
    equity       REAL NOT NULL          -- Total portfolio value in USD
);

-- Telemetry and API reliability logging
CREATE TABLE IF NOT EXISTS api_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    endpoint     TEXT NOT NULL,         -- '/v3/ticker', '/v3/place_order', etc.
    success      INTEGER NOT NULL,      -- 1 = True, 0 = False
    message      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_events_ts ON api_events(timestamp_ms);

-- Machine Learning / Alpha Predictions (optional expansion)
CREATE TABLE IF NOT EXISTS predictions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms     INTEGER NOT NULL,
    pair             TEXT NOT NULL,
    price            REAL NOT NULL,
    target_time_ms   INTEGER NOT NULL,
    predicted_return REAL NOT NULL,
    probability_up   REAL NOT NULL,
    probability_flat REAL NOT NULL,
    probability_down REAL NOT NULL,
    confidence       REAL NOT NULL,
    model_enabled    INTEGER NOT NULL,
    realized_return  REAL,
    realized_state   INTEGER,
    settled          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_time_ms, settled);
CREATE INDEX IF NOT EXISTS idx_predictions_pair ON predictions(pair, timestamp_ms);
```

---

## 3. Fast Warmup Restoration & Drift Correction

Technical indicators (EMA-8, EMA-21, RSI-14) require at least 50 historical price ticks to produce stable, noise-free values. Without persistence, a 5-minute polling bot would have to wait **4.16 hours** (50 ticks × 5 min) before executing a single trade.

The [`_restore_warmup()`](file:///home/ayush/Practice/trade-bot/src/main.py#L330-L375) function solves this cold-start problem:

```
[Bot Startup]
   │
   ├─► 1. Set strategy._restoring = True
   │      (Suppresses stop-loss and take-profit checks while loading historical data)
   │
   ├─► 2. Query SQLite for the last 200 prices per pair in chronological order:
   │      SELECT * FROM prices WHERE pair = ? ORDER BY timestamp_ms DESC LIMIT 200;
   │
   ├─► 3. Replay prices through strategy.update(pair, price)
   │      (Builds EMA-8, EMA-21, and RSI-14 state in memory in milliseconds)
   │
   ├─► 4. Apply Drift Correction:
   │      If the latest database price is older than 10 minutes (e.g. bot was offline),
   │      feed the fresh live price from the exchange to align indicators to current market.
   │
   ├─► 5. Reset Pair Position State:
   │      Set state.entry_price = 0.0, state.hold_cycles = 0, state.last_signal = "HOLD".
   │
   ├─► 6. Set state.just_restored = True:
   │      Forces the strategy to ignore signals on the very first live tick to prevent
   │      phantom crossover orders caused by the boundary between historical and live data.
   │
   └─► 7. Set strategy._restoring = False
          (Live trading is now ready immediately).
```

---

## 4. Useful SQL Audit Queries

You can query the database directly using the SQLite CLI:
```bash
sqlite3 data/bot.sqlite3
```

### 1. View Most Recent Trades
```sql
SELECT 
    datetime(timestamp_ms / 1000, 'unixepoch', 'localtime') AS time_local,
    pair, side, quantity, price, notional, fee, mode, status
FROM trades 
ORDER BY timestamp_ms DESC 
LIMIT 15;
```

### 2. Check Session P&L and Commission Paid
```sql
SELECT 
    mode,
    COUNT(*) AS total_trades,
    SUM(CASE WHEN side = 'BUY' THEN 1 ELSE 0 END) AS total_buys,
    SUM(CASE WHEN side = 'SELL' THEN 1 ELSE 0 END) AS total_sells,
    SUM(notional) AS total_volume_usd,
    SUM(fee) AS total_commission_usd
FROM trades
GROUP BY mode;
```

### 3. Inspect Portfolio Equity Progression
```sql
SELECT 
    datetime(timestamp_ms / 1000, 'unixepoch', 'localtime') AS time_local,
    equity,
    round(equity - 50000, 2) AS pnl_usd,
    round(((equity - 50000) / 50000) * 100, 4) AS pnl_pct
FROM equity 
ORDER BY timestamp_ms DESC 
LIMIT 20;
```

### 4. Check API Failure Rate
```sql
SELECT 
    endpoint,
    COUNT(*) AS total_calls,
    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successful_calls,
    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_calls
FROM api_events 
GROUP BY endpoint;
```

# 08 — Exhaustive File-by-File Reference

This document provides a comprehensive reference explaining the exact purpose, exported classes, functions, dependencies, and inter-module interactions of every single file in the repository.

---

## Table of Contents

1. [Root Directory Files](#1-root-directory-files)
2. [Source Code (`src/`)](#2-source-code-src)
   - [Core Application Files](#21-core-application-files)
   - [API Subsystem (`src/api/`)](#22-api-subsystem-srcapi)
   - [Strategy Subsystem (`src/strategy/`)](#23-strategy-subsystem-srcstrategy)
   - [Risk Subsystem (`src/risk/`)](#24-risk-subsystem-srcrisk)
   - [Logger Subsystem (`src/logger/`)](#25-logger-subsystem-srclogger)
3. [Data Directory (`data/`)](#3-data-directory-data)
4. [Logs Directory (`logs/`)](#4-logs-directory-logs)
5. [Documentation Directory (`docs/`)](#5-documentation-directory-docs)
6. [Implementation Directory (`implementation/`)](#6-implementation-directory-implementation)

---

## 1. Root Directory Files

### [`README.md`](file:///home/ayush/Practice/trade-bot/README.md)
- **Role**: Public repository overview and quickstart guide.
- **Contents**: High-level feature list, quick installation commands, prerequisites (Python 3.10+, Ubuntu EC2), environment variable configuration instructions, competition constraints (1x spot only, $100k starting capital, taker/maker fee rules), and basic usage commands.

### [`plan.md`](file:///home/ayush/Practice/trade-bot/plan.md)
- **Role**: Development roadmap and phase-by-phase engineering milestones.
- **Contents**: Tracks project progression across 6 phases (Foundation, Data & Strategy, Risk Management, Logging & Monitoring, AWS Deployment, Tuning), technology stack decisions, and foundational quantitative concepts.

### [`requirements.txt`](file:///home/ayush/Practice/trade-bot/requirements.txt)
- **Role**: Dependency manifest for Python virtual environment.
- **Contents**: Lists required third-party packages:
  - `requests`: HTTP client for calling Roostoo REST endpoints.
  - `python-dotenv`: Environment variable loader for `.env`.
  - `reportlab`, `pypdf`: PDF generation utilities.

### [`test_bot.py`](file:///home/ayush/Practice/trade-bot/test_bot.py)
- **Role**: Standalone end-to-end integration test and verification script.
- **Key Functions**:
  - `get_wallet(client)`: Fetches SpotWallet from API.
  - `portfolio_value(wallet, tickers)`: Computes total USD valuation across all balances.
  - `print_portfolio(label, wallet, tickers)`: Renders formatted terminal tables showing Free, Locked, and USD values.
  - `main()`: Executes a full connectivity check, collects 30 live price ticks per top-3 pair, evaluates `MomentumStrategy`, places test BUY orders, waits 10s, displays updated P&L, executes closing SELL orders, and displays recent order history from `/v3/query_order`.

### [`h.py`](file:///home/ayush/Practice/trade-bot/h.py)
- **Role**: Real-time terminal portfolio and trade monitor.
- **Key Functions**:
  - `get_entry_prices()`: Queries SQLite `trades` table for the current session to compute net open positions and exact cost basis entry prices.
  - `show()`: Combines live exchange balances (`client.balance()`), live market tickers (`client.get_all_tickers()`), and database entry prices to print a real-time terminal dashboard with unrealized P&L percentages every 30 seconds.

### [`canteen_menu_pdf.py`](file:///home/ayush/Practice/trade-bot/canteen_menu_pdf.py)
- **Role**: Standalone utility script used to merge IIT Kanpur campus canteen menus into a formatted PDF using `reportlab` and `pypdf`. Independent of the trading bot engine.

---

## 2. Source Code (`src/`)

### 2.1 Core Application Files

#### [`src/__init__.py`](file:///home/ayush/Practice/trade-bot/src/__init__.py)
- **Role**: Package marker declaring `src` as a Python package.

#### [`src/main.py`](file:///home/ayush/Practice/trade-bot/src/main.py)
- **Role**: Central orchestrator and execution entry point.
- **Key Classes & Functions**:
  - `TeeStream`: Custom stream wrapper redirecting `sys.stdout` writes simultaneously to the terminal and `logs/bot.log`.
  - `_ISTFormatter`: Logging formatter stamping all records in Indian Standard Time (`UTC+5:30`).
  - `get_exchange_pairs(client)`: Filters tradable pairs from `exchangeInfo()`.
  - `get_amount_precision(exchange_info, pair)` / `get_min_order(exchange_info, pair)`: Precision and limit extractors.
  - `print_header()`, `print_signal()`, `print_trade()`, `print_positions()`, `print_performance()`: Terminal UI formatters.
  - `_detect_market_state(tickers, strategy, pairs)`: Classifies market into 5 regimes (`RECOVERY`, `TRENDING`, `RANGING`, `OVERBOUGHT`, `DOWNTURN`) and dynamically overrides strategy parameters.
  - `_restore_warmup(strategy, db, pairs, live_tickers)`: Loads up to 200 historical prices per pair from SQLite, preloads indicator memory, applies drift correction, and sets `just_restored` flags.
  - `shutdown()`: Handles graceful shutdown, audits open holdings without panic-selling, logs final metrics, and updates SQLite state.
  - `run()`: The infinite 5-minute polling loop coordinating ingestion, valuation, adaptive strategy updates, risk sizing, proactive rotation, order routing, and periodic metrics reporting.

#### [`src/db.py`](file:///home/ayush/Practice/trade-bot/src/db.py)
- **Role**: SQLite data access layer and connection manager.
- **Key Classes & Methods**:
  - `BotDB(db_path)`: Initializes `data/bot.sqlite3`, sets `WAL` mode and `NORMAL` synchronous mode, handles schema migrations, and generates unique session IDs.
  - `set_state(key, value)` / `get_state(key)`: Key-value configuration store.
  - `insert_prices(tickers, timestamp_ms)`: Bulk-inserts ticker snapshots into `prices` table.
  - `get_prices(pair, limit)`: Queries price history for warmup restoration.
  - `insert_trade(...)` / `insert_trade_from_order(order_result, mode)`: Records executed orders into `trades` table.
  - `insert_equity(equity, timestamp_ms)` / `get_equity_history(limit)`: Records and retrieves portfolio equity curve points.
  - `log_api_event(endpoint, success, message)`: Records API call outcomes for reliability monitoring.
  - `stats()`: Returns summary counts for all database tables.

#### [`src/paper_trader.py`](file:///home/ayush/Practice/trade-bot/src/paper_trader.py)
- **Role**: In-memory simulation engine that mirrors Roostoo exchange order execution without placing live trades.
- **Key Classes & Methods**:
  - `Position`: Dataclass holding `coin`, `qty`, and `avg_entry`.
  - `PaperTrader(starting_usd)`: Maintains simulated USD balance, position dictionary, and order log.
  - `get_usd_balance()` / `get_coin_balance(coin)`: Balance inspection helpers.
  - `balance()`: Returns a simulated `SpotWallet` dictionary matching the live API response schema.
  - `place_order(pair, side, quantity, order_type, price)`: Simulates market/limit order fills, calculates 0.1% taker / 0.05% maker commissions, updates positions using weighted average entry pricing, and returns a synthetic `OrderDetail` structure.
  - `portfolio_value(tickers)`: Computes simulated equity using live market prices.
  - `positions_summary(tickers)`: Returns open positions with live unrealized P&L values and percentages.

---

### 2.2 API Subsystem (`src/api/`)

#### [`src/api/__init__.py`](file:///home/ayush/Practice/trade-bot/src/api/__init__.py)
- **Role**: Exposes `RoostooClient` for convenient importing (`from src.api.client import RoostooClient`).

#### [`src/api/client.py`](file:///home/ayush/Practice/trade-bot/src/api/client.py)
- **Role**: Complete HTTP REST client for the Roostoo mock exchange.
- **Key Methods**:
  - `_timestamp()`: Generates 13-digit millisecond timestamps.
  - `_sign(params)`: Implements HMAC-SHA256 request signing over alphabetically sorted parameter strings.
  - `_get(path, params, signed)` / `_post(path, payload)`: Base HTTP request executors with automatic timeout and error handling.
  - `server_time()`: Calls `GET /v3/serverTime`.
  - `exchange_info()`: Calls `GET /v3/exchangeInfo`.
  - `ticker(pair=None)`: Calls `GET /v3/ticker` with timestamp check.
  - `balance()`: Calls signed `GET /v3/balance`.
  - `place_order(pair, side, quantity, order_type, price=None)`: Calls signed `POST /v3/place_order`.
  - `query_order(...)`: Calls signed `POST /v3/query_order`.
  - `cancel_order(...)`: Calls signed `POST /v3/cancel_order`.
  - `short_open(...)` / `short_close(...)`: Calls `/v6` margin endpoints (if enabled).
  - `get_price(pair)` / `get_all_tickers()` / `get_usd_balance()` / `get_coin_balance(coin)`: High-level convenience wrappers.

---

### 2.3 Strategy Subsystem (`src/strategy/`)

#### [`src/strategy/__init__.py`](file:///home/ayush/Practice/trade-bot/src/strategy/__init__.py)
- **Role**: Exposes `MomentumStrategy` and `MomentumConfig`.

#### [`src/strategy/momentum.py`](file:///home/ayush/Practice/trade-bot/src/strategy/momentum.py)
- **Role**: Quantitative signal engine and indicator calculation module.
- **Key Classes & Functions**:
  - `MomentumConfig`: Dataclass holding strategy parameters (EMA periods, RSI window, buy/sell RSI thresholds, minimum separation %, confirmation ticks, stop loss %, take profit %, cooldown cycles).
  - `PairState`: Tracks per-pair rolling price deque, last signal, entry price, hold cycle count, consecutive ticks above slow EMA, and post-loss cooldown cycles.
  - `_ema(prices, period)`: Pure Python Exponential Moving Average calculation.
  - `_rsi(prices, period)`: Pure Python Relative Strength Index calculation.
  - `MomentumStrategy`:
    - `update(pair, price)`: Ingests new price, updates EMA and RSI, checks confirmation windows, evaluates stop-loss / take-profit / signal exit conditions, and returns `"BUY"`, `"SELL"`, or `"HOLD"`.
    - `notify_bought(pair, price)`: Registers entry price and resets hold cycles.
    - `notify_sold(pair, was_loss)`: Clears position and sets cooldown lockout if sold at a loss.
    - `indicators(pair)`: Returns dictionary of current indicators for display and logging.
    - `reset(pair=None)`: Clears in-memory price history.

---

### 2.4 Risk Subsystem (`src/risk/`)

#### [`src/risk/__init__.py`](file:///home/ayush/Practice/trade-bot/src/risk/__init__.py)
- **Role**: Exposes `RiskManager` and `RiskConfig`.

#### [`src/risk/manager.py`](file:///home/ayush/Practice/trade-bot/src/risk/manager.py)
- **Role**: Portfolio risk controller and position sizing engine.
- **Key Classes & Methods**:
  - `RiskConfig`: Dataclass defining max position % (15%), minimum order USD ($10), max drawdown limit (12%), commission rates, and cash reserve % (5%).
  - `RiskManager`:
    - `update_balance(current_total_usd)`: Tracks equity high-water mark; trips circuit breaker (`_halted = True`) if drawdown exceeds 12%.
    - `is_halted()`: Returns True if trading is suspended due to excessive drawdown.
    - `position_size_usd(available_usd, portfolio_value, signal_strength, volatility_pct)`: Computes allocation using Half-Kelly criterion scaled by signal conviction and dampened by asset volatility, subject to hard caps and cash reserves.
    - `quantity_for_usd(usd_amount, price, amount_precision)`: Converts USD allocation to quantity accounting for taker commission and decimal precision.
    - `drawdown(current_total_usd)` / `total_return(current_total_usd)`: Ratio helpers.

---

### 2.5 Logger Subsystem (`src/logger/`)

#### [`src/logger/__init__.py`](file:///home/ayush/Practice/trade-bot/src/logger/__init__.py)
- **Role**: Exposes `TradeLogger`.

#### [`src/logger/trade_logger.py`](file:///home/ayush/Practice/trade-bot/src/logger/trade_logger.py)
- **Role**: Structured file logging and performance evaluation engine.
- **Key Classes & Methods**:
  - `_now_iso()`: Returns current timestamp formatted as ISO-8601 string in IST.
  - `TradeLogger`:
    - `_ensure_trade_log_header()`: Creates `logs/trades.csv` with standard headers if not present.
    - `log_order(order_response, note)`: Extracts execution metadata from order dictionary and appends a row to `logs/trades.csv`.
    - `snapshot(portfolio_usd, extra)`: Appends an equity point to `logs/performance.jsonl`.
    - `performance_summary(initial, current)`: Computes Total Return %, Sharpe Ratio, Sortino Ratio, Calmar Ratio, and Maximum Drawdown % across all recorded portfolio snapshots.

---

## 3. Data Directory (`data/`)

- [`data/bot.sqlite3`](file:///home/ayush/Practice/trade-bot/data/bot.sqlite3): Embedded SQLite database storing `state`, `prices`, `trades`, `equity`, and `api_events`.
- `data/bot.sqlite3-wal`: SQLite Write-Ahead Log journal holding uncommitted/buffered transactions.
- `data/bot.sqlite3-shm`: Shared-memory indexing file for concurrent WAL reads and writes.

---

## 4. Logs Directory (`logs/`)

- `logs/bot.log`: Live text log capturing all terminal output and logger events in IST.
- `logs/trades.csv`: Append-only CSV ledger of all executed trades.
- `logs/performance.jsonl`: JSON Lines file recording portfolio equity snapshots over time.

---

## 5. Documentation Directory (`docs/`)

- [`docs/strategy.md`](file:///home/ayush/Practice/trade-bot/docs/strategy.md): Detailed strategy conceptual guide explaining EMAs, RSI, decision flow, and database tables.
- [`docs/quick-reference.md`](file:///home/ayush/Practice/trade-bot/docs/quick-reference.md): Concise cheat sheet of configuration values, indicator ranges, and SQL queries.
- [`docs/alternative-strategies.md`](file:///home/ayush/Practice/trade-bot/docs/alternative-strategies.md): Reference implementations for Mean Reversion, Bollinger Bands, MACD, and Volume-weighted models.
- [`docs/changelog.md`](file:///home/ayush/Practice/trade-bot/docs/changelog.md): Historical log of code changes, bug fixes, and parameter adjustments.

---

## 6. Implementation Directory (`implementation/`)

- [`implementation/README.md`](file:///home/ayush/Practice/trade-bot/implementation/README.md): Master introduction, learning curriculum, and architectural overview.
- [`implementation/01-architecture-and-execution-flow.md`](file:///home/ayush/Practice/trade-bot/implementation/01-architecture-and-execution-flow.md): System architecture, startup routine, 5-minute poll cycle walkthrough, and shutdown flow.
- [`implementation/02-exchange-api-and-market-data.md`](file:///home/ayush/Practice/trade-bot/implementation/02-exchange-api-and-market-data.md): Roostoo REST API, HMAC-SHA256 signing algorithm, endpoints, and Paper Trader simulation mechanics.
- [`implementation/03-strategy-and-indicators.md`](file:///home/ayush/Practice/trade-bot/implementation/03-strategy-and-indicators.md): Indicator math (EMA, RSI), market regime detection, 2-tick entry confirmation, and 5-tier exit rules.
- [`implementation/04-risk-management-and-capital-allocation.md`](file:///home/ayush/Practice/trade-bot/implementation/04-risk-management-and-capital-allocation.md): Half-Kelly sizing, portfolio constraints, proactive rotation, and drawdown circuit breakers.
- [`implementation/05-data-persistence-and-database.md`](file:///home/ayush/Practice/trade-bot/implementation/05-data-persistence-and-database.md): SQLite WAL schema, fast warmup restoration from disk, drift correction, and SQL audit queries.
- [`implementation/06-logging-and-performance-metrics.md`](file:///home/ayush/Practice/trade-bot/implementation/06-logging-and-performance-metrics.md): Observability, structured files, and mathematical definitions of Sharpe, Sortino, Calmar, and Drawdown.
- [`implementation/07-operations-testing-and-deployment.md`](file:///home/ayush/Practice/trade-bot/implementation/07-operations-testing-and-deployment.md): Setup runbook, Paper/Live mode toggle, diagnostic tools (`test_bot.py`, `h.py`), and Linux/EC2 deployment.
- [`implementation/08-file-by-file-reference.md`](file:///home/ayush/Practice/trade-bot/implementation/08-file-by-file-reference.md): This file — exhaustive tour of every file in the codebase.

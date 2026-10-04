# Trade Bot — Implementation Guide & Learning Hub

Welcome to the internal engineering documentation for the Trade Bot project. This guide is written specifically for software engineers, quantitative analysts, and new contributors who will maintain, extend, or debug this codebase.

There are no marketing buzzwords or hand-wavy explanations here. Everything is described in direct, concrete terms: the exact math, code execution paths, API interactions, risk safeguards, and database persistence mechanisms.

---

## 1. What is this Project?

This project is an **autonomous algorithmic spot trading system** built in Python. It is designed to trade cryptocurrency pairs against USD on the **Roostoo Mock Exchange** (a simulated exchange that mirrors real-world order books and price feeds via REST APIs).

### Core Responsibilities of the System:
1. **Market Data Ingestion**: Continuously polls 80+ spot cryptocurrency pairs at regular intervals (default: 5 minutes).
2. **Persistent Time-Series Storage**: Records price, bid, ask, spread, and volume snapshots into an embedded SQLite database.
3. **Quantitative Signal Generation**: Computes technical indicators (EMA, RSI), evaluates dynamic market regimes (Recovery, Trending, Ranging, Overbought, Downturn), and generates deterministic `BUY`, `SELL`, or `HOLD` signals.
4. **Risk Management & Position Sizing**: Uses half-Kelly criterion scaling based on volatility and signal strength, strictly caps portfolio allocations (max 15% per asset, max 6 concurrent positions), enforces a 20% cash reserve, and acts as a 12% portfolio drawdown circuit breaker.
5. **Execution & Simulation Engine**: Supports both real live authenticated API trading and a zero-risk local Paper Trading mode that accurately simulates taker/maker commissions, slippage, and balance tracking.
6. **Performance & Trade Auditing**: Tracks performance metrics (Total Return, Sharpe Ratio, Sortino Ratio, Calmar Ratio, Max Drawdown) and logs every order to CSV, JSONL, and SQLite tables.

---

## 2. Documentation Roadmap

To get up to speed with the project, read through the documentation in the following sequence:

| Document | Topic | Description |
|---|---|---|
| [01. Architecture & Execution Flow](file:///home/ayush/Practice/trade-bot/implementation/01-architecture-and-execution-flow.md) | System Design & Main Loop | How all components connect, lifecycle of a 5-minute poll cycle, state management, and shutdown procedures. |
| [02. Exchange API & Market Data](file:///home/ayush/Practice/trade-bot/implementation/02-exchange-api-and-market-data.md) | REST API & Paper Engine | Roostoo API endpoints, HMAC-SHA256 authentication, order mechanics, commission structures, and how `PaperTrader` simulates trades locally. |
| [03. Strategy & Indicators](file:///home/ayush/Practice/trade-bot/implementation/03-strategy-and-indicators.md) | Signal Generation & Math | EMA & RSI formulas, dynamic market regime detection, 2-tick entry confirmation, trailing stops, profit hurdles, and cooldown locks. |
| [04. Risk Management & Capital Allocation](file:///home/ayush/Practice/trade-bot/implementation/04-risk-management-and-capital-allocation.md) | Position Sizing & Safety | Half-Kelly position sizing, cash reserves, single-coin exposure caps, portfolio drawdown halts, and active position rotation. |
| [05. Data Persistence & Database](file:///home/ayush/Practice/trade-bot/implementation/05-data-persistence-and-database.md) | SQLite & Warmup Engine | SQLite database schema, WAL mode, session tracking, fast warmup restoration from disk, and useful SQL audit queries. |
| [06. Logging & Performance Metrics](file:///home/ayush/Practice/trade-bot/implementation/06-logging-and-performance-metrics.md) | Observability & Math | Structured logging, IST timestamping, and exact mathematical formulations of Sharpe, Sortino, Calmar, and Drawdown. |
| [07. Operations, Testing & Deployment](file:///home/ayush/Practice/trade-bot/implementation/07-operations-testing-and-deployment.md) | Deployment & Runbook | Setup guide, environment configuration, switching Paper/Live modes, running diagnostics, and EC2/Linux service deployment. |
| [08. File-by-File Reference](file:///home/ayush/Practice/trade-bot/implementation/08-file-by-file-reference.md) | Exhaustive Codebase Tour | Detailed breakdown of every single file in the repository, explaining its purpose, exported interfaces, and dependencies. |

---

## 3. High-Level Directory Overview

```
trade-bot/
├── config/                      # Configuration settings and environment definitions
├── data/                        # Embedded SQLite database storage
│   ├── bot.sqlite3              # Main SQLite database file
│   ├── bot.sqlite3-wal          # Write-Ahead Log (WAL) journal file
│   └── bot.sqlite3-shm          # Shared-memory index for WAL
├── docs/                        # Legacy / quick strategy notes
│   ├── alternative-strategies.md
│   ├── changelog.md
│   ├── quick-reference.md
│   └── strategy.md
├── implementation/              # Complete implementation & architectural documentation (you are here)
│   ├── README.md
│   ├── 01-architecture-and-execution-flow.md
│   ├── 02-exchange-api-and-market-data.md
│   ├── 03-strategy-and-indicators.md
│   ├── 04-risk-management-and-capital-allocation.md
│   ├── 05-data-persistence-and-database.md
│   ├── 06-logging-and-performance-metrics.md
│   ├── 07-operations-testing-and-deployment.md
│   └── 08-file-by-file-reference.md
├── logs/                        # Runtime logs and metrics
│   ├── bot.log                  # Mirror of stdout console output
│   ├── performance.jsonl        # JSON-lines portfolio equity history
│   └── trades.csv               # CSV record of all executed trades
├── src/                         # Production application source code
│   ├── api/                     # Roostoo exchange REST client and signing
│   ├── logger/                  # CSV/JSONL trade logger and snapshot engine
│   ├── risk/                    # Position sizing and portfolio risk manager
│   ├── strategy/                # Momentum strategy and technical indicators
│   ├── db.py                    # SQLite connection manager and query layer
│   ├── main.py                  # Main execution entry point and polling loop
│   └── paper_trader.py          # Local exchange simulator for dry-run testing
├── canteen_menu_pdf.py          # Standalone PDF compilation utility
├── h.py                         # Live portfolio monitor and terminal dashboard
├── plan.md                      # High-level development plan
├── README.md                    # Project overview and quick start
├── requirements.txt             # Python dependency manifest
└── test_bot.py                  # End-to-end integration and smoke test script
```

---

## 4. Key Design Principles

1. **No External Heavy Frameworks**: Indicators and calculations (EMA, RSI, Kelly, Ratios) are implemented using pure Python math and standard library data structures (`collections.deque`, `math`, `sqlite3`). This keeps the bot lightning fast, easy to debug, and free of dependency conflicts.
2. **Defensive by Default**: All trading decisions must pass multiple layers of verification: minimum history warmup, RSI momentum boundaries, EMA separation thresholds, position limits, cash reserve limits, and loss lockout cooldowns.
3. **Commission-Aware Economics**: In spot trading with 0.10% taker fees (0.20% round-trip), frequent churning destroys capital. The bot enforces minimum hold durations, minimum profit exit thresholds (+0.25%), and minimum order sizes to ensure every trade has positive expected value.
4. **Resilient State Recovery**: If the bot restarts or crashes, it automatically queries SQLite on startup to preload up to 200 price ticks per pair, restores technical indicators instantly, reconciles existing wallet holdings, and avoids firing duplicate orders.

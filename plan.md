# Development Plan

## Goal

Build an autonomous trading bot that connects to the Roostoo mock exchange, executes spot trades, and optimizes for risk-adjusted returns (Sharpe, Sortino, Calmar ratios).

---

## Phase 1 — Foundation (Day 1)

### 1.1 Understand the API
- [ ] Read Roostoo API documentation thoroughly
- [ ] Identify all available endpoints (market data, order placement, portfolio status)
- [ ] Test API calls manually with `curl` or Postman before writing code

### 1.2 Project Scaffold
- [ ] Set up Python project structure (`src/`, `config/`, `logs/`, `tests/`)
- [ ] Create `requirements.txt` with pinned dependencies
- [ ] Set up environment variable handling (no hardcoded keys)

### 1.3 API Client
- [ ] Build a reusable API client class with GET/POST methods
- [ ] Implement HMAC signing if required by Roostoo
- [ ] Add retry logic and error handling for failed requests
- [ ] Log every API request and response status

---

## Phase 2 — Data and Strategy (Day 1–2)

### 2.1 Market Data Pipeline
- [ ] Fetch real-time price data for all available assets
- [ ] Store recent price history in memory (rolling window)
- [ ] Compute technical indicators (e.g., RSI, EMA, MACD, Bollinger Bands)

### 2.2 Trading Strategy
Choose and implement at least one core strategy:

**Option A — Momentum / Trend Following**
- Buy assets trending upward (price above N-period EMA)
- Sell when trend reverses
- Simple, well-understood, works in trending markets

**Option B — Mean Reversion**
- Buy when price is significantly below its moving average
- Sell when it reverts to mean
- Works in range-bound markets

**Option C — Multi-signal Hybrid**
- Combine momentum + volatility + volume signals
- Score each asset and rank-allocate capital

### 2.3 Signal Generation
- [ ] Define entry and exit conditions clearly
- [ ] Avoid over-fitting — keep rules simple and explainable
- [ ] Paper-test logic against historical or live data before deploying

---

## Phase 3 — Risk Management (Day 2)

### 3.1 Position Sizing
- [ ] Never allocate more than X% of portfolio to one asset (e.g., 10–20%)
- [ ] Scale position size by signal confidence and volatility

### 3.2 Stop-Loss and Take-Profit
- [ ] Implement per-trade stop-loss (e.g., -3% to -5%)
- [ ] Optional: trailing stop to lock in gains

### 3.3 Portfolio-Level Controls
- [ ] Track total exposure (long vs. cash)
- [ ] Pause trading if drawdown exceeds threshold (e.g., -10% from peak)
- [ ] Respect commission costs in profit calculations (0.1% per taker order)

---

## Phase 4 — Logging and Monitoring (Day 2)

### 4.1 Trade Logging
- [ ] Log every order: timestamp, asset, side, quantity, price, status
- [ ] Log API errors and retries separately

### 4.2 Performance Tracking
- [ ] Track portfolio value over time
- [ ] Compute returns, drawdown, Sharpe ratio, Sortino ratio, Calmar ratio
- [ ] Write performance summary to a log file periodically

### 4.3 Alerting (optional)
- [ ] Print periodic status to stdout (visible via SSH/screen)

---

## Phase 5 — AWS Deployment (Day 2–3)

### 5.1 EC2 Setup
- [ ] Launch EC2 instance from Roostoo-provided AWS sub-account
- [ ] Install Python, pip, and project dependencies
- [ ] Clone repo and configure environment variables securely

### 5.2 Persistent Execution
- [ ] Run bot in `screen` or `tmux` session so it survives SSH disconnection
- [ ] Alternatively, set up a `systemd` service for auto-restart on crash

### 5.3 Monitoring
- [ ] SSH in periodically to check logs
- [ ] Verify bot is still running and executing trades as expected

---

## Phase 6 — Tuning (Day 3 onwards)

- [ ] Review live trade logs and identify losing patterns
- [ ] Adjust strategy parameters (window sizes, thresholds) based on observed behavior
- [ ] Do not over-tune — keep changes conservative and evidence-based

---

## Key Metrics to Optimize

| Metric | What It Measures |
|--------|-----------------|
| Portfolio Return | Total % gain over competition period |
| Sharpe Ratio | Return per unit of total volatility |
| Sortino Ratio | Return per unit of downside volatility |
| Calmar Ratio | Return relative to max drawdown |

---

## Tech Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.10+ |
| HTTP Client | `requests` (with retry via `urllib3`) |
| Data/Math | `pandas`, `numpy` |
| Indicators | `ta` or manual implementation |
| Deployment | AWS EC2, `screen` / `systemd` |
| Logging | Python `logging` module + CSV/JSON files |

---

## What to Learn First

1. **REST APIs** — how to make authenticated HTTP requests (GET/POST, headers, HMAC signing)
2. **Python `requests` library** — the primary tool for API calls
3. **Technical indicators** — RSI, EMA, MACD basics (just enough to implement one strategy)
4. **Risk management basics** — position sizing, stop-loss, drawdown
5. **AWS EC2** — launching an instance, SSH, running a persistent process
6. **Ratio math** — Sharpe, Sortino, Calmar formulas

---

## Resources

- Roostoo API Docs: provided by organizers
- Roostoo App (leaderboard):
  - iOS: https://apps.apple.com/us/app/roostoo-mock-crypto-trading/id1483561353
  - Android: https://play.google.com/store/apps/details?id=com.roostoo.roostoo
  - Web: https://app.roostoo.com
- Python `requests` docs: https://requests.readthedocs.io/
- `pandas-ta` indicators: https://github.com/twopirllc/pandas-ta
- Investopedia — Sharpe Ratio: https://www.investopedia.com/terms/s/sharperatio.asp
- Investopedia — Sortino Ratio: https://www.investopedia.com/terms/s/sortinoratio.asp
- Investopedia — Calmar Ratio: https://www.investopedia.com/terms/c/calmarratio.asp
- AWS EC2 Getting Started: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html

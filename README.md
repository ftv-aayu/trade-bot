# trade-bot

A prediction-only crypto market bot that fetches public Binance data, runs an ML ensemble to score trade signals, and displays results in a terminal dashboard. No API key required. No live trading.

---

## What it does

- **Fetches public market data** from `data-api.binance.vision` (crypto, no auth) and Yahoo Finance via `yfinance` (stocks, no auth).
- **Prefetches 3000 bars of crypto history** per symbol on startup (~50 hours of 1m data) and **60 days of 5m stock history**.
- **Runs ML predictions** on each universe independently using a walk-forward gated ridge regression model and a 3-class softmax classifier (UP / FLAT / DOWN).
- **Scores signals** through a regime-aware strategy layer that weighs 18 technical features against detected market conditions.
- **Respects market hours** — stock polling is skipped automatically when US markets are closed (nights, weekends).
- **Persists everything** to a local SQLite database (auto-created under `data/`).
- **Settles predictions** after the forecast horizon passes and tracks directional accuracy separately for crypto and stocks.
- **Displays a live terminal dashboard** powered by [Rich](https://github.com/Textualize/rich) with separate panels for crypto and stocks.

---

## Project structure

```
trade-bot/
├── src/
│   ├── __init__.py
│   ├── __main__.py             # Entry point for python -m src
│   ├── config.py               # All configuration via environment variables
│   ├── predictor.py            # ML ensemble: ridge regression + softmax classifier
│   ├── db.py                   # SQLite persistence layer
│   ├── main.py                 # Polling loop
│   ├── api/
│   │   ├── __init__.py
│   │   └── client.py           # Binance public REST client
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── boss.py             # Signal scoring and regime detection
│   │   └── portfolio_optimizer.py  # Covariance-aware weight optimizer
│   ├── logger/
│   │   ├── __init__.py
│   │   └── trade_logger.py     # JSONL performance log
│   └── risk/
│       └── __init__.py
├── dashboard.py                # Rich terminal dashboard
├── requirements.txt
├── .env.example                # Reference for all environment variables
├── data/                       # SQLite DB (auto-created on first run)
└── logs/                       # bot.log + performance.jsonl
```

---

## Setup

### 1. Get the code

```bash
cd ~/Projects/trade-bot
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure (optional)

```bash
cp .env.example .env
```

The defaults work out of the box. No API key needed. The only values worth changing are `WATCH_SYMBOLS` (which pairs to track) and `POLL_SECONDS` (default: 60).

---

## Running

**Terminal 1 — the bot:**
```bash
source .venv/bin/activate
python -m src
```

**Terminal 2 — the dashboard:**
```bash
source .venv/bin/activate
python dashboard.py
```

### Cold-start timeline

| Time | What happens |
|------|--------------|
| 0:00 | Bot starts, begins prefetching klines |
| ~0:07 | Crypto prefetch done — 3000 bars × 20 symbols (60 Binance requests) |
| ~2:00 | Stock prefetch done — 60 days × 5m bars × 20 tickers (Yahoo Finance) |
| ~2:30 | First poll, both models train |
| **~2:30** | **Crypto + stock predictions appear on dashboard** |
| Every 30 min | Both models retrain on accumulated live data |
| Every 60 min | Predictions settle and accuracy updates |

Crypto alone starts in ~1 min. Set `STOCKS_ENABLED=false` to skip the stock prefetch.

---

## How the ML model works

See [`LOGIC.md`](LOGIC.md) for the full technical explanation.

Short version: a **walk-forward gated ridge regression** predicts the expected return over the next 60 minutes. A **3-class softmax classifier** (UP / FLAT / DOWN) is trained in parallel on the same 18 features. Both are validated on a held-out time split before any predictions are trusted — the model stays silent if it cannot demonstrate an edge on out-of-sample data.

---

## Extending further

Stocks are already supported out of the box. For other asset classes (futures, forex, ETFs):

- Replace or extend `src/api/stock_client.py` with a client for the new data provider.
- Add the new symbols to a new `WATCH_<CLASS>` env var and wire it into `main.py`.
- The strategy, predictor, database, and dashboard panels require no changes.

## Requirements

- Python 3.11+
- Internet access (Binance public API for crypto; Yahoo Finance for stocks)
- No API keys required for either data source

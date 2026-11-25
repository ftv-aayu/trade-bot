# trade-bot

A prediction-only crypto market bot that fetches public Binance data, runs an ML ensemble to score trade signals, and displays results in a terminal dashboard. No API key required. No live trading.

---

## What it does

- **Fetches public market data** from `data-api.binance.vision` — Binance's dedicated no-auth market data endpoint. 1-minute OHLCV candles, no account or API key needed.
- **Prefetches 3000 bars of history** per symbol on startup (~50 hours of 1m data), so the ML model is ready to predict within ~1 minute of launch.
- **Runs ML predictions** on each configured symbol using a walk-forward gated ridge regression model and a 3-class softmax classifier (UP / FLAT / DOWN).
- **Scores signals** through a regime-aware strategy layer that weighs 18 technical features against detected market conditions (risk-on / balanced / risk-off / defensive).
- **Persists everything** to a local SQLite database (auto-created under `data/`).
- **Settles predictions** after the forecast horizon passes and tracks directional accuracy over time.
- **Displays a live terminal dashboard** powered by [Rich](https://github.com/Textualize/rich) that reads directly from the database.

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
| ~0:07 | Prefetch complete — 3000 bars × 20 symbols stored (60 HTTP requests) |
| ~1:00 | First poll cycle finishes, model trains on ~51,000 samples |
| **~1:00** | **First predictions appear on dashboard** |
| Every 30 min | Model retrains on accumulated live data |
| Every 60 min | Predictions from 1 hour ago are settled and accuracy is updated |

There is no long waiting period. The bot starts predicting within ~1 minute of launch.

---

## How the ML model works

See [`LOGIC.md`](LOGIC.md) for the full technical explanation.

Short version: a **walk-forward gated ridge regression** predicts the expected return over the next 60 minutes. A **3-class softmax classifier** (UP / FLAT / DOWN) is trained in parallel on the same 18 features. Both are validated on a held-out time split before any predictions are trusted — the model stays silent if it cannot demonstrate an edge on out-of-sample data.

---

## Extending to stocks

The bot is designed for this. When ready:

- Replace `src/api/client.py` with a client for a stock data provider (e.g. `yfinance`, Alpha Vantage, or a broker API) that returns the same OHLCV structure.
- Update `WATCH_SYMBOLS` in `.env` to stock tickers (`AAPL`, `SPY`, etc.).
- Lower `PREDICTOR_MIN_SAMPLES` for daily candles (252 is one year; 5000 daily bars would be ~20 years).
- Add a market-hours guard in `src/main.py` to skip polling when the exchange is closed.
- The strategy, predictor, database, and dashboard layers need no changes.

---

## Requirements

- Python 3.11+
- Internet access for Binance public API (bot only; dashboard works fully offline)
- No Binance account or API key

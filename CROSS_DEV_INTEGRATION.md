# Cross-Dev Integration Contract
## Avengers (trade-bot) ↔ Team Nexus (EduMind)

**Plugin title:** Paper Trading Practice Arena  
**Built by:** Team Avengers (265001–265007)  
**Integrates into:** EduMind — Adaptive Learning Intelligence Platform (Team Nexus)  
**Demo slot:** Thu Nov 26, 2026, 9 PM IST (Slot 1)  
**Our server:** `http://localhost:8000` (run `python arena_server.py`)  
**Swagger docs:** `http://localhost:8000/docs`

---

## How to run our server

```bash
cd ~/Projects/trade-bot
source .venv/bin/activate
python arena_server.py              # starts on port 8000
python arena_server.py --port 8080  # custom port
```

Swagger UI at `http://localhost:8000/docs` — every endpoint has a "Try it out" button.

---

## Overview

EduMind teaches trading concepts and tracks per-student topic mastery. Our plugin adds hands-on practice: a student gets a **virtual $100,000 wallet** and trades against **live market prices** (Binance + Yahoo Finance). At the end of a session the plugin scores the student 0–1 and returns a JSON payload ready to POST to EduMind's quiz-results endpoint.

---

## Our API — what EduMind calls on us

Base URL: `http://localhost:8000`

---

### GET /health
Check if our server is up and market is open.

**Response:**
```json
{
  "status": "ok",
  "prices_in_db": 173080,
  "predictions_in_db": 8980,
  "us_market_open": false,
  "mins_until_open": 868,
  "crypto_symbols": 20,
  "stock_tickers": 20
}
```

---

### GET /prices?symbol=BTCUSDT
Get live price for one or all symbols.

**Without symbol param** — returns all 40 prices (20 crypto + 20 stocks):
```json
{
  "prices": {
    "BTCUSDT": 60423.13,
    "ETHUSDT": 1677.56,
    "AAPL":    229.04,
    "NVDA":    141.11
  },
  "count": 40,
  "timestamp": "2026-11-26T05:32:10+05:30"
}
```

**With `?symbol=BTCUSDT`:**
```json
{
  "symbol": "BTCUSDT",
  "price": 60423.13,
  "timestamp": "2026-11-26T05:32:10+05:30"
}
```

---

### GET /predictions?asset_type=crypto
Get the ML model's latest signal for every symbol.

**Query params:**
- `asset_type` — `crypto`, `stock`, or omit for both
- `limit` — max results (default 40)

**Response:**
```json
{
  "predictions": [
    {
      "symbol":           "BTCUSDT",
      "asset_type":       "crypto",
      "predicted_return": 0.000487,
      "probability_up":   0.36,
      "probability_flat": 0.31,
      "probability_down": 0.33,
      "confidence":       0.19,
      "model_enabled":    true,
      "signal":           "HOLD",
      "timestamp":        "2026-11-26T05:17:24+05:30"
    }
  ],
  "count": 40
}
```

`signal` is `"BUY"` / `"SELL"` / `"HOLD"` — the buy/sell/hold advisory for each symbol.

---

### GET /predictions/{symbol}
Get the signal for one specific symbol.

```
GET /predictions/NVDA
```

```json
{
  "symbol":           "NVDA",
  "asset_type":       "stock",
  "predicted_return": 0.001200,
  "probability_up":   0.34,
  "probability_flat": 0.39,
  "probability_down": 0.27,
  "confidence":       0.18,
  "model_enabled":    true,
  "signal":           "BUY",
  "timestamp":        "2026-11-26T02:27:23+05:30"
}
```

---

### GET /accuracy
Live accuracy of the ML model on settled predictions.

```json
{
  "crypto":  { "n": 7160, "directional_accuracy": 0.443 },
  "stock":   { "n": 1340, "directional_accuracy": 0.464 },
  "overall": { "n": 8500, "directional_accuracy": 0.448 }
}
```

---

### POST /traders/register
Create a virtual wallet for a student. If already exists, returns current state.

**Request:**
```json
{
  "trader_id":    "s_12345",
  "starting_cash": 100000
}
```

**Response:**
```json
{
  "trader_id":     "s_12345",
  "is_new":        true,
  "cash":          100000.0,
  "total_value":   100000.0,
  "starting_cash": 100000.0
}
```

---

### GET /traders/{trader_id}/portfolio
Get current portfolio at live prices.

```
GET /traders/s_12345/portfolio
```

**Response:**
```json
{
  "trader_id":       "s_12345",
  "cash":            95000.0,
  "total_value":     99995.0,
  "positions_value": 4995.0,
  "unrealized_pnl":  -5.0,
  "pnl_pct":         -0.005,
  "starting_cash":   100000.0,
  "positions": [
    {
      "symbol":             "BTCUSDT",
      "qty":                0.082669,
      "avg_cost":           60422.01,
      "current_price":      60361.5,
      "value":              4990.0,
      "unrealized_pnl":     -5.0,
      "unrealized_pnl_pct": -0.1
    }
  ],
  "timestamp": "2026-11-26T05:35:00+05:30"
}
```

---

### POST /traders/buy
Buy a symbol at the current live price.

**Request:**
```json
{
  "trader_id":  "s_12345",
  "symbol":     "BTCUSDT",
  "amount_usd": 5000
}
```

**Response:**
```json
{
  "ok":        true,
  "trader_id": "s_12345",
  "side":      "BUY",
  "symbol":    "BTCUSDT",
  "qty":       0.082669,
  "price":     60422.01,
  "spent_usd": 5000.0,
  "fee_usd":   5.0,
  "timestamp": "2026-11-26T05:32:13+05:30"
}
```

**Errors:**
```json
{ "detail": "Symbol 'FAKECOIN' not found or not watched" }   // 400
{ "detail": "Insufficient cash" }                            // 400
```

---

### POST /traders/sell
Sell a % of a held position.

**Request:**
```json
{
  "trader_id": "s_12345",
  "symbol":    "BTCUSDT",
  "percent":   50
}
```
*(percent defaults to 100 if omitted — full position close)*

**Response:**
```json
{
  "ok":           true,
  "trader_id":    "s_12345",
  "side":         "SELL",
  "symbol":       "BTCUSDT",
  "qty":          0.041335,
  "price":        60500.0,
  "proceeds_usd": 2500.60,
  "fee_usd":      2.50,
  "realized_pnl": 3.20,
  "timestamp":    "2026-11-26T05:40:00+05:30"
}
```

---

### GET /traders/{trader_id}/orders?limit=50
Order history for a trader.

```json
{
  "trader_id": "s_12345",
  "orders": [
    {
      "id":        1,
      "side":      "BUY",
      "symbol":    "BTCUSDT",
      "qty":       0.082669,
      "price":     60422.01,
      "notional":  5000.0,
      "fee":       5.0,
      "timestamp": "2026-11-26T05:32:13+05:30",
      "note":      "api"
    }
  ],
  "count": 1
}
```

---

### GET /traders/{trader_id}/score
Get the session score and the EduMind quiz-result payload.

```
GET /traders/s_12345/score
```

**Response:**
```json
{
  "trader_id":                  "s_12345",
  "total_value":                101840.50,
  "pnl_pct":                    1.84,
  "score":                      0.72,
  "pnl_component":              0.62,
  "diversification_component":  0.80,
  "discipline_component":       0.75,
  "unique_symbols_traded":      4,
  "total_orders":               7,
  "edumind_payload": {
    "plugin_id":   "paper-trading-arena",
    "student_id":  "s_12345",
    "session_id":  "sess_1795651336",
    "timestamp":   "2026-11-26T00:02:16Z",
    "topic":       "risk_management",
    "score":       0.72,
    "max_score":   1.0,
    "breakdown": {
      "pnl_component":             0.62,
      "diversification_component": 0.80,
      "discipline_component":      0.75
    },
    "details": {
      "starting_cash":        100000.0,
      "ending_value":         101840.5,
      "pnl_pct":              1.84,
      "unique_symbols_traded": 4,
      "total_orders":          7
    }
  }
}
```

Take `edumind_payload` and POST it directly to EduMind's quiz-results endpoint.

---

### POST /traders/{trader_id}/reset
Reset a trader back to $100k (wipes all positions and orders).

```
POST /traders/s_12345/reset
```

```json
{ "ok": true, "trader_id": "s_12345", "cash": 100000.0 }
```

---

### GET /leaderboard
Live leaderboard across all registered traders.

```json
{
  "leaderboard": [
    { "rank": 1, "trader_id": "Alice",   "total_value": 103200.0, "pnl_pct": 3.2,  "cash": 50000, "positions": 3 },
    { "rank": 2, "trader_id": "Bob",     "total_value": 101500.0, "pnl_pct": 1.5,  "cash": 90000, "positions": 1 },
    { "rank": 3, "trader_id": "AutoBot", "total_value": 98400.0,  "pnl_pct": -1.6, "cash": 98400, "positions": 0 }
  ],
  "count": 3,
  "timestamp": "2026-11-26T05:50:00+05:30"
}
```

---

## EduMind webhooks — what you send us

### POST /webhooks/edumind
Send events here whenever a student's mastery changes or EduMind issues new recommendations.

**mastery.updated:**
```json
{
  "event":      "mastery.updated",
  "student_id": "s_12345",
  "timestamp":  "2026-11-26T09:00:00Z",
  "changes": {
    "risk_management": { "old": 0.28, "new": 0.31 }
  }
}
```

**recommendations.updated:**
```json
{
  "event":                 "recommendations.updated",
  "student_id":            "s_12345",
  "timestamp":             "2026-11-26T09:00:00Z",
  "recommended_topics":    ["risk_management", "portfolio_theory"]
}
```

**We always respond within 1s:**
```json
{ "received": true, "event": "mastery.updated", "student_id": "s_12345" }
```

**To view received events (debug):**
```
GET /webhooks/edumind/log
```

---

## Score breakdown

| Component | Weight | How it's calculated |
|-----------|--------|---------------------|
| P&L | 50% | Maps ±20% profit/loss to 0–1 score |
| Diversification | 30% | Unique symbols traded ÷ 5 (capped at 1.0) |
| Discipline | 20% | Fraction of sells that cut a losing position |

Fully rule-based — every point is explainable to the student. No ML in the score.

---

## What EduMind must provide to us

1. **Student mastery endpoint** — `GET /api/v1/students/{id}/mastery` returning mastery dict (mock data is fine for demo)
2. **Quiz results endpoint** — `POST /api/v1/students/{id}/quiz-results` accepting our `edumind_payload`
3. **Plugin iframe tab** — embed `http://localhost:8000/docs` or our React frontend URL
4. **Webhook delivery** — POST `mastery.updated` to `http://our-server/webhooks/edumind` after mastery changes

---

## Quick integration test (curl)

```bash
# 1. Register a student
curl -X POST http://localhost:8000/traders/register \
  -H "Content-Type: application/json" \
  -d '{"trader_id": "s_12345"}'

# 2. Get a signal
curl http://localhost:8000/predictions/NVDA

# 3. Buy based on signal
curl -X POST http://localhost:8000/traders/buy \
  -H "Content-Type: application/json" \
  -d '{"trader_id": "s_12345", "symbol": "NVDA", "amount_usd": 3000}'

# 4. Check portfolio (live P&L)
curl http://localhost:8000/traders/s_12345/portfolio

# 5. Sell
curl -X POST http://localhost:8000/traders/sell \
  -H "Content-Type: application/json" \
  -d '{"trader_id": "s_12345", "symbol": "NVDA", "percent": 100}'

# 6. Get score + EduMind payload
curl http://localhost:8000/traders/s_12345/score

# 7. Send a webhook event (simulate EduMind)
curl -X POST http://localhost:8000/webhooks/edumind \
  -H "Content-Type: application/json" \
  -d '{"event": "mastery.updated", "student_id": "s_12345", "changes": {"risk_management": {"old": 0.28, "new": 0.31}}}'
```

---

*Last updated: 2026-11-26. *

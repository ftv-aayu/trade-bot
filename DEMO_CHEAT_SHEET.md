# Demo Cheat Sheet — Slot 1, Thu Nov 26, 9:00 PM IST

> Print this. Every command in order. Test the whole flow once before 8:30 PM.

---

## 0. Setup (before demo starts, ~15 min early)

```bash
cd ~/Projects/trade-bot
source .venv/bin/activate

# Terminal 1 — the trading engine (keep running all demo)
python -m src

# Terminal 2 — ML dashboard (leave visible on projector side)
python dashboard.py
```

Wait ~1 minute, confirm dashboard shows fresh predictions.

---

## 1. Self-dev intro (2 min) — the trading engine

- "Ingestion: Binance public market data, no API keys, WebSocket-grade refresh."
- "Normalizer: everything lands in one standard format in SQLite."
- "ML layer: walk-forward gated ridge regression + 3-class classifier on 18 features."
- Point at dashboard: **crypto + stock predictions, regime, accuracy panels.**
- "The model gates itself — it only predicts when out-of-sample validation passes."

## 2. ML component (2 min)

- Point at accuracy panels: crypto n≈7,000+, stocks n≈1,300+ settled predictions.
- "Directional accuracy tracked live; predictions settle after the 60-minute horizon."

---

## 3. Cross-dev plugin — Paper Trading Practice Arena (main event, ~8 min)

### 3.1 New student gets a wallet

```bash
# Terminal 3
python paper_arena.py --trader Alice
```

> "EduMind launches our plugin from its Plugins tab. Alice is a student with
> low mastery in risk management. The plugin gives her a $100,000 virtual
> wallet and a live market — prices come straight from our trading engine."

### 3.2 Place trades (ask a teammate to be the student)

Inside the arena:
```
> buy
Symbol: BTCUSDT
Amount in USD: 5000

> buy
Symbol: NVDA
Amount in USD: 3000
```

> "Orders fill locally at live prices — nothing is ever sent to Binance.
> That's the safety line: real market, zero real money."

### 3.3 Show the live portfolio

Type `refresh`. Point at:
- Positions updating with live P&L
- The **Bot Predictions panel** — "the engine's ML signals are shown to the
  student as advisory hints; the API returns buy/sell/hold suggestions"

### 3.4 Second student (shows the arena scales)

```bash
# Terminal 4
python paper_arena.py --trader Bob
```
Bob buys something else, then in either terminal:
```bash
# Terminal 5 (or let Alice's screen scroll to it)
python paper_arena.py --leaderboard
```

> "Every session is scored and the result is sent back to EduMind."

### 3.5 Close the session and score it

Alice types `quit`. Show the score panel. Then in a spare terminal:

```bash
python paper_arena.py --score Alice
```

JSON output appears — this IS the payload posted to EduMind:

```json
{
  "trader_id": "Alice",
  "total_value": 100842.10,
  "pnl_pct": 0.84,
  "score": 0.61,
  "pnl_component": 0.52,
  "diversification_component": 0.60,
  "discipline_component": 0.80,
  "unique_symbols_traded": 3,
  "total_orders": 5
}
```

> "Score = 50% P&L, 30% diversification, 20% discipline — whether the
> student actually cut a losing position. No ML in the score, so every
> point is explainable to the student. This posts back to EduMind's
> quiz-result API, and EduMind updates Alice's mastery."

### 3.6 The autonomous trader (flourish — also demos our ML)

```bash
# Terminal 6
python auto_trader.py --dry-run --trader "EduMind-Bot"
```

> "The same predictions can also drive an autonomous paper trader.
> Here it is in dry-run — it shows the signals it would act on:
> confidence-weighted entries, 1.5% stop-loss, 2.5% take-profit."

If asked to make it trade live: `python auto_trader.py --trader "EduMind-Bot" --min-conf 0.18 --min-return 0.0003`

---

## 4. Talking points if judges ask

**"Is any real money at risk?"** — No. Paper orders fill locally against live prices. Binance is read-only public data; we never place orders.

**"How does the plugin talk to EduMind?"** — Via their OpenAPI REST spec: we read each student's mastery, subscribe to `mastery.updated` / `recommendations.updated` webhooks, and post each scored session back as a quiz result. Contract doc: `CROSS_DEV_INTEGRATION.md`.

**"Why is the score rule-based, not ML?"** — Deliberate. Every score must be explainable to a student. See CROSS_DEV_INTEGRATION.md.

**"What's the ML accuracy?"** — Point at dashboard panels; be honest: it varies with regime, the gate keeps it silent when it has no demonstrated edge.

**"What data does the plugin store?"** — Its own SQLite tables: wallets, paper orders, positions, session results. Validated on ingest (bad rows dropped).

---

## 5. Emergency fallbacks

| If this breaks | Do this |
|---|---|
| Binance unreachable | Prices fall back to last DB snapshot; keep demoing |
| Dashboard empty | Restart it; it reads the DB, no state lost |
| Arena weird | `python paper_arena.py --reset Alice` |
| Everything on fire | `python auto_trader.py --dry-run` still shows the ML signals offline from DB |

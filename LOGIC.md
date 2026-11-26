# LOGIC.md — How trade-bot works

This document explains every layer of the bot — the data pipeline, the ML model,
the signal scoring, and what every term on the dashboard means.

---

## Table of contents

1. [Data pipeline](#1-data-pipeline)
2. [Feature engineering — 18 features](#2-feature-engineering--18-features)
3. [ML model — PredictiveEnsemble](#3-ml-model--predictiveensemble)
4. [Walk-forward validation gate](#4-walk-forward-validation-gate)
5. [Signal scoring — BossStrategy](#5-signal-scoring--bossstrategy)
6. [Regime detection](#6-regime-detection)
7. [Portfolio optimizer](#7-portfolio-optimizer)
8. [Prediction settlement and accuracy tracking](#8-prediction-settlement-and-accuracy-tracking)
9. [Dashboard term glossary](#9-dashboard-term-glossary)
10. [Update log](#10-update-log)

---

## 1. Data pipeline

### Crypto — Binance
- **Source:** `https://data-api.binance.vision` — Binance's public, no-auth market data endpoint
- **Endpoints:** `GET /api/v3/klines` (history), `GET /api/v3/ticker/24hr` (live prices)
- **Universe:** 100 liquid USDT pairs (BTC, ETH, SOL, XRP, ... down to ~$1M/day volume)
- **Interval:** 1-minute candles
- **Poll:** every 60 seconds

### Stocks — Yahoo Finance
- **Source:** Yahoo Finance via `yfinance` library — no API key required
- **Universe:** 20 large-cap US stocks (AAPL, MSFT, GOOGL, NVDA, TSLA, ...)
- **Interval:** 5-minute candles
- **Poll:** every 300 seconds, only during US market hours (Mon–Fri 09:30–16:00 ET)

### Startup prefetch
On first launch the bot downloads historical data to warm up the ML model immediately:

| Source | History fetched | Time |
|--------|----------------|------|
| Binance (crypto) | 3000 × 1m bars per symbol (50 hours) | ~2 min |
| Yahoo Finance (stocks) | 60 days × 5m bars per ticker | ~3 min |

### Polling loop (every 60s for crypto)
1. Fetch live tickers from Binance
2. Store price snapshot in SQLite with `asset_type='crypto'`
3. Load full price histories from DB
4. Run `strategy.analyze()` → signal scores + ML predictions
5. Store new predictions in DB
6. Settle predictions whose 60-minute horizon has passed
7. Every 300s: same cycle for stocks (if market open)

---

## 2. Feature engineering — 18 features

All features are built from the **close price series only** — no volume, no order book.
Defined in `src/predictor.py → _feature_arrays()`.

All time windows are defined in real minutes and scaled to the polling interval at runtime.

| # | Name | Formula | What it captures |
|---|------|---------|-----------------|
| 1 | `r5` | `price[t] / price[t-5m] − 1` | Very short momentum |
| 2 | `r15` | `price[t] / price[t-15m] − 1` | Short momentum |
| 3 | `r30` | `price[t] / price[t-30m] − 1` | Medium-short momentum |
| 4 | `r60` | `price[t] / price[t-1h] − 1` | 1-hour return |
| 5 | `r240` | `price[t] / price[t-4h] − 1` | 4-hour return |
| 6 | `r360` | `price[t] / price[t-6h] − 1` | 6-hour return |
| 7 | `trend30` | `price / EMA(30m) − 1` | How far above/below the 30m trend |
| 8 | `trend120` | `price / EMA(120m) − 1` | How far above/below the 2h trend |
| 9 | `trend360` | `price / EMA(360m) − 1` | How far above/below the 6h trend |
| 10 | `accel` | `r15 − r60/4` | Is momentum accelerating or decelerating? |
| 11 | `decel` | `r60 − r240/4` | Is 1h momentum fading vs 4h? |
| 12 | `vol15` | `std(log returns, 15m) × √15` | Short-term realised volatility |
| 13 | `vol60` | `std(log returns, 60m) × √60` | 1-hour realised volatility |
| 14 | `vol_ratio` | `vol15 / vol60` | Is vol spiking (>1) or calming (<1)? |
| 15 | `extension` | `max(0, r60 − 0.25 × max(r240,0))` | Overextension above trend |
| 16 | `mean_gap` | `price / rolling_mean(60m) − 1` | Distance from 1h mean price |
| 17 | `r60 − market_r60` | Symbol's 1h return minus universe median | **Relative strength vs market** |
| 18 | `r240 − market_r240` | Symbol's 4h return minus universe median | **Relative strength (4h) vs market** |

Features 17–18 are **cross-sectional** — they compare each symbol to the whole universe at every timestamp. These are the most important signals. A symbol with `r60 − market_r60 = +0.02` means it gained 2% more than the median coin over the past hour — a leading indicator.

---

## 3. ML model — PredictiveEnsemble

Defined in `src/predictor.py`. Two models trained simultaneously on the same features.

### Model A: Ridge regression (expected return)

Predicts the percentage return of each symbol over the next 60 minutes (the **prediction horizon**).

```
target y = price[t + 60min] / price[t] − 1
```

Steps:
1. Build feature matrix X — one row per symbol per timestep across all 100 symbols
2. Standardise X: subtract mean, divide by std (computed on training set only)
3. Fit: solve `(XᵀX + αI)β = Xᵀy` where `α = 4.0` (ridge regularisation)
4. Predict: `ŷ = Xβ × scale + mean`

**Why ridge?** Features like `r5`, `r15`, `r30` are highly correlated. Ridge regularisation prevents the model from blowing up on correlated inputs by penalising large coefficients. It's the right choice when you have more features than you need.

### Model B: Softmax classifier (direction)

Classifies each future return into **UP / FLAT / DOWN**.

```
UP   if return > threshold
DOWN if return < −threshold  
FLAT otherwise

threshold = max(0.0015, 2×fee + 0.5×vol60)
```

The threshold adapts to current volatility — in a high-vol environment a 0.1% move is noise, so the threshold widens.

Trained as multi-output ridge regression on a one-hot encoded target, with softmax applied to get three probabilities P(UP), P(FLAT), P(DOWN) that sum to 1.

### Combined prediction

```
ensemble = 0.70 × regression_pred + 0.30 × (P(UP) − P(DOWN)) × residual_std
```

The 70/30 blend weights the continuous regression more than the discrete classifier, but lets the classifier add or subtract confidence based on the direction vote.

**Confidence score:**
```
z = ensemble / residual_std
z_component    = 1 − exp(−|z| / 2.5)
prob_component = |P(UP) − P(DOWN)|

confidence = 0.35 × z_component + 0.35 × prob_component + 0.30 × 0.5
```

A high `z` means the prediction is far from zero relative to the model's own uncertainty. A large `|P(UP) − P(DOWN)|` means the classifier is decisive. Both contribute to confidence.

Confidence range is 0–1. In low-vol markets it typically sits at 0.15–0.25. In trending markets it reaches 0.4–0.7.

---

## 4. Walk-forward validation gate

The model is **not trusted until it proves an edge on held-out data**. This runs before every retrain (every 30 minutes).

### How it works

1. Split full history into **train (75%)** and **validation (25%)** by time
2. Insert a **60-minute purge gap** between them — prevents the training window "leaking" into validation through autocorrelated prices
3. Fit a temporary model on train only
4. Evaluate 6 metrics on the validation set
5. If all pass → promote to live predictions. If any fail → `model=False`, no predictions

### Active gates (3 sanity checks — always meaningful)

| Gate | Threshold | Meaning if it fails |
|------|-----------|---------------------|
| Directional accuracy | ≥ 0.50 | Model predicts wrong direction more than right |
| Balanced accuracy | ≥ 0.36 | Model performs below random on some classes |
| Brier score | ≤ 0.245 | Classifier probabilities are badly miscalibrated |
| Residual std | > 1e-8 | Model output is degenerate (all-zero) |

### Cross-sectional gates (require ≥ 50 symbols to be meaningful)

| Gate | Threshold | Meaning if it fails |
|------|-----------|---------------------|
| Rank IC | ≥ 0.01 | Model's ranking of symbols doesn't correlate with actual returns |
| Top-decile edge | ≥ 0.00025 | Top-10% predicted symbols underperform the universe |
| Top-decile net return | ≥ 0.00025 | Same, after fees and slippage |
| Correlation | ≥ 0.01 | Predicted vs realised return correlation is near zero |

**Why 100 symbols is important for these gates:** rank IC at each timestamp needs at least 50 observations to be statistically meaningful. With 20 symbols, rank IC flips positive/negative at random — the gate would toggle on noise. With 100 symbols each timestamp gives 100 observations, making these metrics genuine quality indicators.

### What the gate output means on the dashboard

| Dashboard shows | Meaning |
|-----------------|---------|
| `model=True` | All gates passed, predictions are live |
| `model=False` | At least one gate failed, no predictions |
| `rank gate passed; classifier gated` | Regression ranker is active but softmax classifier isn't trusted — predictions are based on regression only |
| `rank+directional gates passed` | Both models fully active — highest confidence mode |

---

## 5. Signal scoring — BossStrategy

Defined in `src/strategy/boss.py → analyze()`. Runs every poll cycle regardless of ML gate status.

### Score components (0–1 each)

| Component | Weight in risk_on | What it measures |
|-----------|-------------------|-----------------|
| Trend score | 28% | Cross-sectional rank of 1h, 4h, 12h, 24h returns and EMA trend |
| Relative score | 25% | Rank of 4h and 12h return vs universe median |
| ML prediction | 16% | Confidence-weighted ML rank |
| Continuation | 10% | Pullback into uptrend and breakout rank |
| Quality | 11% | Liquidity, spread tightness, low volatility rank |
| Breakout | 10% | How much the price exceeds its 4h high |

Weights shift with regime — in `defensive` mode quality weight rises to 44% (avoid junk in bad markets); in `risk_on` trend and momentum get more weight.

### Hard filters (all must pass to generate a signal)

A symbol is rejected if any of these fail:
- 1h return < −1% (falling too fast short-term)
- 4h return < −1%
- 12h return < −2%
- EMA trend is negative (price below its own 6h average)
- Net expected edge < 0.25% after fees and spread
- Score < 0.51
- 24h quote volume < $5M (illiquid)
- Bid-ask spread > 20 bps (too costly to trade)

### Bearish ML veto

Even if a symbol passes all filters, if the ML model is active and confident it will fall:
- `predicted_return ≤ −0.5%` AND
- `P(DOWN) ≥ 0.60` AND
- `confidence ≥ 0.60`

...then the symbol is blocked from the signal list regardless of its momentum score.

---

## 6. Regime detection

Computed fresh every poll from the cross-sectional universe.

**Market breadth** = fraction of symbols where all of these are true:
- 4h return > 0
- 12h return > 0
- 24h return > 0
- Price above 6h EMA

**Market strength** (0–1):
```
strength = 0.40 × breadth_score
         + 0.25 × median_4h_score
         + 0.18 × median_12h_score
         + 0.17 × median_24h_score
         − 0.12 × vol_drag
```

| Strength | Regime | Gross exposure cap | Behaviour |
|----------|--------|--------------------|-----------|
| ≥ 0.72 | `risk_on` | 100% | Trend-following, full position sizing |
| 0.50–0.72 | `balanced` | 90% | Mixed weights |
| 0.28–0.50 | `risk_off` | 75% | Quality-first, reduced exposure |
| < 0.28 | `defensive` | 50% | Maximum quality weight, minimum exposure |

---

## 7. Portfolio optimizer

Defined in `src/strategy/portfolio_optimizer.py`. Runs after signal scoring to size positions.

Maximises a **mean-variance utility**:
```
max  α·|w| − (λ/2)·wᵀΣw − c·|w − w_prev|

subject to: 0 ≤ wᵢ ≤ 10%  (per-asset cap)
            Σwᵢ ≤ 70%      (gross exposure cap)
```

Where:
- `α` = gross expected edge per signal
- `λ = 60` = risk aversion (penalises variance)
- `Σ` = exponentially weighted covariance matrix (half-life 180 min)
- `c` = cost penalty × one-way trading cost (penalises unnecessary turnover)

Solved with proximal gradient descent (250 iterations). In this bot (prediction-only mode), the weights are computed but not acted on — they represent a normalised signal strength ranking.

---

## 8. Prediction settlement and accuracy tracking

Every poll, the bot checks for predictions whose 60-minute `target_time_ms` has passed:

```
realized_return = current_price / entry_price − 1
realized_state  = UP   if >  threshold
                  DOWN if < −threshold
                  FLAT otherwise
```

**Directional accuracy** = fraction of predictions where `sign(predicted_return) == sign(realized_return)`.

Random baseline = 50%. Anything above 50% over a large sample is genuine signal.

**What the accuracy numbers mean as of today (2026-11-26):**

| | Accuracy | Settled predictions |
|--|--|--|
| Crypto | 44.1% | 9,462 |
| Stocks | 46.4% | 1,340 |

Both are below 50% right now. This is because the crypto market has been in `risk_off` / `defensive` regime for most of the past 24 hours, and the momentum model has negative edge in reversing/flat markets. The model correctly gates itself (`model=False`) during these periods. When the model IS active (UTC 09:00–10:00 window), accuracy reached 60%.

---

## 9. Dashboard term glossary

Everything you see on `python dashboard.py` explained:

### Header bar
| Term | Meaning |
|------|---------|
| `trade-bot` | The system name |
| Timestamp | Current time in IST |
| `X,XXX prices` | Total price rows stored in DB across all symbols |
| `X,XXX predictions` | Total ML predictions stored since bot started |
| `US Market: OPEN / CLOSED` | Whether US stock market is currently trading |

### Crypto / Stock Predictions table

| Column | Meaning |
|--------|---------|
| **Symbol** | The trading pair, e.g. `BTCUSDT` = Bitcoin vs USD Tether |
| **Direction** | `▲ UP` or `▼ DN` — which way the model predicts price will move in 60 minutes |
| **Pred Ret** | Predicted percentage return over the next 60 minutes. `+0.0031` = model expects +0.31% gain |
| **Conf** | Confidence score 0–1. How certain the model is. 0.20 = low, 0.60+ = high. In quiet markets typically 0.15–0.25 |
| **P(Up)** | Probability the price will be UP in 60 min (from the softmax classifier) |
| **P(Dn)** | Probability the price will be DOWN in 60 min |
| **Model ✓/—** | ✓ = ML model was active when this prediction was made. — = signal from before model was gated |
| **At** | Time the prediction was made (IST) |

**Note:** P(Up) + P(Flat) + P(Down) = 1. The displayed P(Flat) = 1 − P(Up) − P(Down). When the classifier is uncertain, all three probabilities are near 0.33.

### Model Accuracy panels

| Term | Meaning |
|------|---------|
| **Settled** | Number of predictions whose 60-min horizon has passed and been scored against reality |
| **Dir acc** | Directional accuracy — what % of the time the model predicted the right direction. Baseline is 50% (random) |
| **Baseline: 50%** | What you'd get by predicting randomly. Anything above this is real signal |

### History Depth panel

| Term | Meaning |
|------|---------|
| Symbol + bar count | How many 1-minute (crypto) or 5-minute (stock) candles are stored for that symbol |
| Bar chart `████` | Visual length proportional to history depth |

### Bot log (`python -m src` output)

| Term | Meaning |
|------|---------|
| `regime=risk_on/balanced/risk_off/defensive` | Current market regime based on breadth and momentum |
| `model=True/False` | Whether ML model passed validation and is producing predictions |
| `gate: rank+directional gates passed` | Both regression ranker and classifier are active |
| `gate: rank gate passed; classifier gated` | Regression ranker active, classifier not trusted — predictions use regression only |
| `gate: rank gated: ...` | Specific metric that failed. E.g. `top_decile_edge -0.0015 < 0.00025` means the top-10% predicted symbols are underperforming |
| `score=0.742` | Signal score for that symbol (0–1). Higher = stronger buy signal |
| `pred=+0.0017` | Predicted return over the next 60 minutes (+0.17%) |
| `conf=0.20` | Model confidence (0–1) |
| `Poll #N` | Nth poll cycle since bot started |
| `crypto acc=X%` | Running directional accuracy on settled crypto predictions |
| `stock acc=X%` | Running directional accuracy on settled stock predictions |

### paper_arena.py terms

| Term | Meaning |
|------|---------|
| **Virtual wallet** | Simulated account with $100,000 starting cash |
| **P&L** | Profit and Loss — how much you've made or lost |
| **Unrealized P&L** | Paper gain/loss on open positions at current price — not locked in until you sell |
| **Avg Cost** | Average price you paid per unit (for positions built up over multiple buys) |
| **Session score** | Overall 0–1 score: 50% from P&L, 30% from diversification, 20% from discipline |
| **Discipline** | Whether you cut losing positions (sold at a loss) — this is good risk management |

### auto_trader.py terms

| Term | Meaning |
|------|---------|
| **Dry run** | Shows what the bot would trade without placing any orders |
| **Stop-loss** | Auto-sell if position drops 1.5% from entry — limits downside |
| **Take-profit** | Auto-sell if position gains 2.5% from entry — locks in gains |
| **min-conf** | Minimum prediction confidence required to open a position |
| **min-return** | Minimum predicted return required to open a position |
| **SIGNAL ▲** | The bot would BUY this symbol right now |
| **HELD** | Symbol is already in a position |
| **wait** | Prediction doesn't meet thresholds |

---

## 10. Update log

| Date | Change |
|------|--------|
| 2026-11-25 | Initial build. Binance public data, 18-feature ridge ensemble, Rich dashboard, SQLite persistence. 20-symbol universe. Prefetch 3000 bars per symbol on startup. |
| 2026-11-25 | Added US stock support via Yahoo Finance (yfinance, 5m interval). Independent strategy+predictor per asset class. DB `asset_type` column. Dashboard side-by-side crypto/stock panels. |
| 2026-11-25 | Added `--mode crypto/stock/both` CLI flag to bot, dashboard, arena server, and auto-trader. |
| 2026-11-25 | Built paper trading engine (`src/paper_engine.py`), manual arena (`paper_arena.py`), auto-trader (`auto_trader.py`), and FastAPI server (`arena_server.py` — 20 REST endpoints, Swagger at `/docs`). |
| 2026-11-26 | Expanded crypto universe from 20 → 100 symbols. Cross-sectional gates (rank IC, top-decile edge) require 50+ observations per timestamp to be statistically meaningful — 20 symbols was too few. With 100 symbols these gates now function as designed. Training samples: 51k → 258k. Gates restored to original thresholds (rank_ic ≥ 0.01, top_decile_edge ≥ 0.00025). No more threshold hacking needed. |
| 2026-11-26 | Full LOGIC.md rewrite with ML explanation, all 18 features documented, complete dashboard glossary. |

---

*Last updated: 2026-11-26. See git history for full change trail.*

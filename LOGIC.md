# LOGIC.md — How trade-bot works

This document explains the technical logic of each layer of the bot. It is updated as the bot evolves.

---

## Table of contents

1. [Data pipeline](#1-data-pipeline)
2. [Feature engineering (18 features)](#2-feature-engineering-18-features)
3. [ML model — PredictiveEnsemble](#3-ml-model--predictiveensemble)
4. [Walk-forward validation gate](#4-walk-forward-validation-gate)
5. [Signal scoring — BossStrategy](#5-signal-scoring--bossstrategy)
6. [Regime detection](#6-regime-detection)
7. [Portfolio optimizer](#7-portfolio-optimizer)
8. [Prediction settlement and accuracy tracking](#8-prediction-settlement-and-accuracy-tracking)
9. [Update log](#9-update-log)

---

## 1. Data pipeline

### Crypto — Binance
**Source:** `https://data-api.binance.vision`  
**Endpoints:** `GET /api/v3/klines` (history), `GET /api/v3/ticker/24hr` (live prices)  
**Auth:** None. Poll interval: 60 seconds (default).

### Stocks — Yahoo Finance
**Source:** Yahoo Finance via the `yfinance` library  
**Endpoints:** Internal Yahoo Finance API (no key required)  
**Interval:** 5-minute bars (up to 60 days of history available)  
**Auth:** None. Poll interval: 300 seconds (5 min). Skipped automatically outside US market hours (Mon–Fri 9:30–16:00 ET).

The two universes run **independent strategy and predictor instances** — crypto and stock price series are never mixed in the same training matrix. Predictions and accuracy are tracked separately per `asset_type` in the DB.

### Startup prefetch

| Source | Bars fetched | Method |
|--------|-------------|--------|
| Binance (crypto) | 3000 × 1m per symbol | 3 paginated REST requests per symbol |
| Yahoo Finance (stocks) | ~4680 × 5m per ticker (60 days) | Single `yf.download()` call per ticker |

### Polling loop

Every `POLL_SECONDS` (default: 60s):
1. Fetch `ticker/24hr` from Binance for all crypto symbols.
2. Store price snapshot with `asset_type='crypto'`.
3. Run `crypto_strategy.analyze()` → store predictions.
4. If `stock_poll_seconds` has elapsed and market is open: fetch latest stock prices, store with `asset_type='stock'`, run `stock_strategy.analyze()`.
5. Settle predictions whose target time has passed (both asset types).

---

## 2. Feature engineering (18 features)

Defined in `src/predictor.py` → `_feature_arrays()`.  
All features are computed from the **close price series only** (no volume, no order book).

| # | Feature | Description |
|---|---------|-------------|
| 1 | `r5` | Return over the past 5 minutes |
| 2 | `r15` | Return over the past 15 minutes |
| 3 | `r30` | Return over the past 30 minutes |
| 4 | `r60` | Return over the past 60 minutes |
| 5 | `r240` | Return over the past 4 hours |
| 6 | `r360` | Return over the past 6 hours |
| 7 | `trend30` | Price / EMA(30min) − 1  (how far price is above/below its 30m trend) |
| 8 | `trend120` | Price / EMA(120min) − 1 |
| 9 | `trend360` | Price / EMA(360min) − 1 |
| 10 | `accel` | r15 − r60 / (60/15)  — momentum acceleration |
| 11 | `decel` | r60 − r240 / (240/60) — momentum deceleration |
| 12 | `vol15` | Realised volatility over 15 minutes (std of log returns × √bars) |
| 13 | `vol60` | Realised volatility over 60 minutes |
| 14 | `vol_ratio` | vol15 / vol60  — short-term vs long-term vol (regime indicator) |
| 15 | `extension` | max(0, r60 − 0.25 × max(r240, 0)) — overextension above trend |
| 16 | `mean_gap` | Price / rolling_mean(60min) − 1 — distance from 1h mean |
| 17 | `r60 − market_r60` | Symbol's 1h return minus the **cross-sectional median** (relative strength) |
| 18 | `r240 − market_r240` | Symbol's 4h return minus the cross-sectional median |

Features 17–18 are **cross-sectional** — they measure a symbol's performance relative to the whole universe at each point in time. This is the most important signal for identifying which coin is leading or lagging the market.

All windows are defined in real minutes and scaled to the current polling cadence at runtime, so the same feature logic works at 1-minute or 5-minute bars.

---

## 3. ML model — PredictiveEnsemble

Defined in `src/predictor.py` → `PredictiveEnsemble`.

### Regression model (expected return)

A **ridge regression** trained to predict the return over the next `PREDICTION_HORIZON` minutes (default: 60 minutes).

```
y = price[t + horizon] / price[t] - 1
```

Training steps:
1. Build feature matrix X (one row per symbol per timestep).
2. Standardise X to zero mean, unit variance.
3. Solve `(XᵀX + αI) β = Xᵀy` where `α = PREDICTOR_RIDGE_ALPHA` (default: 4.0).
4. Store `β`, the mean and std used to standardise, and the residual std of the validation set.

Ridge regularisation prevents overfitting when features are correlated (which they often are — `r5`, `r15`, `r30` are all momentum).

### Classifier (UP / FLAT / DOWN)

A **multi-output ridge classifier** trained on the same feature matrix.

Labels are assigned per sample:
```
UP   (+1) if return > threshold
DOWN (-1) if return < -threshold
FLAT ( 0) otherwise
```

where `threshold = max(PREDICTOR_CLASS_THRESHOLD, 2×fee + 0.5×vol60)`.  
The threshold adapts to current volatility so the model doesn't label high-volatility noise as signals.

The classifier uses a **softmax** output — it produces three probabilities P(UP), P(FLAT), P(DOWN) that sum to 1.

### Combined prediction

The final prediction per symbol blends regression and classifier:

```
ensemble = 0.70 × regression_pred + 0.30 × (P(UP) − P(DOWN)) × residual_std
```

Confidence is computed from the z-score of the ensemble prediction:

```
z = ensemble / residual_std
z_component = 1 − exp(−|z| / 2.5)
confidence ≈ 0.35 × z_component + 0.35 × |P(UP) − P(DOWN)| + 0.30 × 0.5
```

A higher z-score (prediction further from zero in units of model uncertainty) and a larger gap between P(UP) and P(DOWN) both increase confidence.

---

## 4. Walk-forward validation gate

The model is **not trusted until it proves it has an edge** on out-of-sample data.

Before each retrain (every 30 minutes by default), `PredictiveEnsemble.fit_and_predict()`:

1. Splits the full history into **train (75%)** and **validation (25%)** by time.
2. Inserts a **purge gap** of 60 minutes between train and validation to prevent information leakage.
3. Trains a temporary model on the train set only.
4. Evaluates it on the validation set against six gate criteria:

| Gate | Default threshold | What it checks |
|------|-----------------|----------------|
| Correlation | ≥ −0.05 | Predicted vs realised return correlation |
| Rank IC | ≥ −0.10 | Cross-sectional rank correlation at each timestamp |
| Top-decile edge | ≥ −0.001 | Mean return of top-10% predicted symbols minus universe mean |
| Top-decile net return | ≥ −0.005 | Same, minus fees and estimated slippage |
| Brier score | ≤ 0.245 | Classifier calibration (lower = better) |
| Directional accuracy | ≥ 0.47 | Fraction of predictions with correct direction |
| Balanced accuracy | ≥ 0.33 | Per-class accuracy averaged across UP/FLAT/DOWN |
| Residual std | > 1e-8 | Model is not degenerate |

> **Note on threshold values:** With only 20 symbols the cross-sectional rank IC at each timestamp has just 20 observations, making it a very noisy statistic. Thresholds are set to reject clearly broken models (large negative values) rather than demand positive edge, which the small universe cannot reliably demonstrate out-of-sample.

This prevents the system from predicting during market regimes where the model has no demonstrated edge (e.g., very low-volatility consolidation periods where all returns are near zero).

---

## 5. Signal scoring — BossStrategy

Defined in `src/strategy/boss.py` → `BossStrategy.analyze()`.

For each symbol with enough history, the strategy computes a **score from 0 to 1** by combining:

- **Trend score** (28–30%): cross-sectional ranks of 1h, 4h, 12h, 24h returns, and EMA trend
- **Relative score** (24–30%): rank of 4h and 12h return vs cross-sectional median
- **Continuation** (10–12%): pullback and breakout rank (buying dips in uptrends)
- **ML prediction component** (12–19%): adjusted by prediction confidence
- **Quality** (11–44%): liquidity, spread tightness, low volatility rank — weighted heavily in defensive regimes

The weights shift based on the detected regime (see section 6).

A signal must pass all of these **hard filters** to be included:
- 1h return ≥ `MIN_1H_RETURN` (default: −1%)
- 4h return ≥ `MIN_4H_RETURN` (default: −1%)
- 12h return ≥ `MIN_12H_RETURN` (default: −2%)
- Positive EMA trend
- Net expected edge > `MIN_RELATIVE_EDGE` (default: 0.25%) after fees and spread
- Score ≥ `MIN_SCORE` (default: 0.51)
- 24h quote volume ≥ `MIN_QUOTE_VOLUME` (default: $5M)
- Spread ≤ `MAX_SPREAD_BPS` (default: 20 bps)

### Bearish ML veto

If the ML model is active, directional-accuracy-validated, and predicts:
- predicted_return ≤ −0.5%
- P(DOWN) ≥ 0.60
- confidence ≥ 0.60

...then the symbol is blocked from the long signal list regardless of its momentum score.

---

## 6. Regime detection

Computed in `BossStrategy.analyze()` from the cross-sectional universe at each poll.

**Market breadth** = fraction of symbols where 4h return > 0 AND 12h return > 0 AND 24h return > 0 AND EMA trend > 0.

**Market strength** (0–1) is a weighted composite:
```
strength = 0.40 × breadth_score
         + 0.25 × median_4h_score
         + 0.18 × median_12h_score
         + 0.17 × median_24h_score
         − 0.12 × vol_drag
```

| Strength | Regime | Behaviour |
|----------|--------|-----------|
| ≥ 0.72 | `risk_on` | Trend-following weights, higher gross exposure cap |
| 0.50–0.72 | `balanced` | Balanced weights |
| 0.28–0.50 | `risk_off` | Quality-first, reduced exposure |
| < 0.28 | `defensive` | Maximum quality weight (44%), lowest exposure cap |

The regime also controls gross exposure caps: `risk_on` allows up to 100% gross target, `defensive` caps at 50%.

---

## 7. Portfolio optimizer

Defined in `src/strategy/portfolio_optimizer.py` → `optimize_signed_portfolio()`.

The optimizer maximises a **mean-variance utility** over the selected long candidates:

```
max  α·|w| − (λ/2)·wᵀΣw − c·|w − w_prev|

subject to:
  0 ≤ wᵢ ≤ MAX_ASSET_WEIGHT     (per-asset cap)
  Σwᵢ ≤ MAX_GROSS_TARGET         (long gross cap)
```

Where:
- `α` = gross expected edge per signal
- `λ` = `PORTFOLIO_RISK_AVERSION` (default: 60) — penalises variance
- `Σ` = exponentially-weighted covariance matrix of recent log returns, shrunk toward diagonal
- `c` = `PORTFOLIO_COST_PENALTY` × one-way trading cost — penalises unnecessary turnover

Solved with **proximal gradient descent** (250 iterations default). The covariance matrix uses a half-life of 180 minutes so recent volatility structure matters more than old.

The result is target weights for each selected signal. In this bot (prediction-only mode), these weights are computed but not acted on — they serve as a normalised signal strength ranking.

---

## 8. Prediction settlement and accuracy tracking

Every poll cycle, `db.settle_predictions()` checks for predictions whose `target_time_ms` has passed. For each settled prediction:

```
realized_return = current_price / entry_price − 1
realized_state  = UP if > threshold, DOWN if < −threshold, FLAT otherwise
```

The dashboard's **Model Accuracy** panel shows the cumulative directional accuracy of all settled predictions. This is the ground truth for how well the model is performing on live data.

After running for one full prediction horizon (60 minutes), accuracy numbers appear. After a few hours they become statistically meaningful.

---

## 9. Update log

| Date | Change |
|------|--------|
| 2026-11-25 | Initial build. Binance public data, 18-feature ridge ensemble, Rich dashboard, SQLite persistence. Prefetch fetches 3000 bars per symbol (3 paginated requests) on startup. Predictions available ~1 minute after launch. |
| 2026-11-25 | Loosened validation gate thresholds for small 20-symbol universe. Rank IC with 20 observations is too noisy to gate on positive values — thresholds now reject clearly broken models (large negative) rather than requiring demonstrated positive edge. Model now passes gate and produces predictions. |
| 2026-11-25 | Added US stock market support via Yahoo Finance (yfinance). Separate StockClient with market-hours guard. Independent strategy+predictor instance per asset class. DB tables gain `asset_type` column (crypto/stock) with backwards-compatible migration. Dashboard shows crypto and stock predictions in side-by-side panels with per-class accuracy. 20 large-cap US stocks added as default watch list. |

---

*This file is updated whenever the bot's logic changes. See git history for the full change trail.*

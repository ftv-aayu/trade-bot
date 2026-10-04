# 06 — Logging & Performance Metrics

This document explains the logging infrastructure, structured telemetry formats, and the exact mathematical formulas used to evaluate trading performance in [`src/logger/trade_logger.py`](file:///home/ayush/Practice/trade-bot/src/logger/trade_logger.py) and [`src/main.py`](file:///home/ayush/Practice/trade-bot/src/main.py).

---

## 1. Logging Infrastructure

The system employs a multi-tiered logging strategy:

```
[Main Application / Loggers]
             │
             ├──► TeeStream ───────┬──► Terminal (sys.stdout)
             │                     └──► logs/bot.log (Live text stream)
             │
             ├──► TradeLogger ─────┬──► logs/trades.csv (Executed orders)
             │                     └──► logs/performance.jsonl (Equity snapshots)
             │
             └──► BotDB ───────────┴──► data/bot.sqlite3 (WAL tables)
```

### 1.1 `TeeStream` Dual Output
Implemented in `src/main.py`, `TeeStream` wraps `sys.stdout` so that every `print()` and standard library `logging` statement simultaneously outputs to the interactive terminal and appends to `logs/bot.log`. This ensures full console visibility when running in background sessions (`screen` or `tmux`).

### 1.2 Timezone Stamping (`_ISTFormatter`)
All log lines are formatted with Indian Standard Time (`IST` = `UTC+5:30`) timestamps to match operational monitoring schedules:
```
2026-10-04 14:30:00 IST [INFO] main: Cycle 12: warmed=86/86 avg_ticks=50.0 portfolio=50124.50 usd_free=42500.00
```

---

## 2. Structured Log Files

### 2.1 `logs/trades.csv`
Every filled order (both paper and live) is appended to this CSV file with the following headers:

| Field | Type | Description | Example |
|---|---|---|---|
| `timestamp` | ISO-8601 | Local IST timestamp | `2026-10-04T14:30:00.123456+05:30` |
| `pair` | String | Trading pair symbol | `BTC/USD` |
| `side` | String | Trade direction | `BUY` or `SELL` |
| `type` | String | Order execution type | `MARKET` or `LIMIT` |
| `quantity` | Float | Base asset quantity filled | `0.0205` |
| `price` | Float | Average execution price | `84250.00` |
| `order_id` | String | Exchange or paper order ID | `1048576` |
| `status` | String | Order status | `FILLED` |
| `commission_usd` | Float | Commission fee charged | `1.7271` |
| `note` | String | Trigger context | `live_buy`, `proactive_exit`, `rotation_sell` |

### 2.2 `logs/performance.jsonl`
Every cycle, a JSON object is appended to record the portfolio equity curve:
```json
{"timestamp": "2026-10-04T14:30:00.123456+05:30", "portfolio_usd": 50124.50}
```

---

## 3. Quantitative Performance Metrics (Formulas & Code)

At regular intervals (`PERF_INTERVAL = 900` seconds / 15 minutes) and upon shutdown, the bot calculates risk-adjusted performance ratios across all recorded snapshots $V = [v_0, v_1, v_2, \dots, v_n]$.

### 3.1 Step 1: Discrete Period Returns
The sequence of percentage returns between consecutive equity snapshots is:
$$R_i = \frac{v_{i} - v_{i-1}}{v_{i-1}}, \quad \text{for } i = 1, \dots, n-1$$

- **Mean Return ($\bar{R}$)**: $\bar{R} = \frac{1}{m} \sum_{i=1}^m R_i$
- **Total Standard Deviation ($\sigma$)**: $\sigma = \sqrt{\frac{1}{m} \sum_{i=1}^m (R_i - \bar{R})^2}$
- **Downside Deviation ($\sigma_d$)**: Considers only negative returns ($R_i < 0$):
  $$\sigma_d = \sqrt{\frac{1}{k} \sum_{R_i < 0} R_i^2}$$

---

### 3.2 Total Return %
Measures total capital growth since bot inception:
$$\text{Total Return \%} = \left(\frac{\text{Current Value} - \text{Initial Balance}}{\text{Initial Balance}}\right) \times 100$$

---

### 3.3 Maximum Drawdown (MDD)
Measures the largest peak-to-trough decline experienced by the portfolio:
$$\text{Peak}_t = \max(v_0, v_1, \dots, v_t)$$
$$\text{Drawdown}_t = \frac{\text{Peak}_t - v_t}{\text{Peak}_t}$$
$$\text{Max Drawdown \%} = \max_{t}(\text{Drawdown}_t) \times 100$$

---

### 3.4 Sharpe Ratio
Measures the excess return generated per unit of total risk (volatility):
$$\text{Sharpe Ratio} = \left(\frac{\bar{R}}{\sigma}\right) \times \sqrt{m}$$
*(Where $\sqrt{m}$ scales the ratio to the observed sample window).*

---

### 3.5 Sortino Ratio
Unlike Sharpe (which penalizes both upward and downward volatility), the Sortino ratio penalizes **only downside volatility**. This makes it a superior metric for asymmetric trend-following strategies:
$$\text{Sortino Ratio} = \left(\frac{\bar{R}}{\sigma_d}\right) \times \sqrt{m}$$

---

### 3.6 Calmar Ratio
Measures return relative to maximum drawdown risk. It answers: *“How much return was made per dollar of worst-case paper loss?”*
$$\text{Calmar Ratio} = \frac{\text{Total Return Fraction}}{\text{Max Drawdown Fraction}}$$

---

## 4. Implementation in `TradeLogger`

From [`src/logger/trade_logger.py:performance_summary()`](file:///home/ayush/Practice/trade-bot/src/logger/trade_logger.py#L101-L147):

```python
def performance_summary(self, initial: float, current: float) -> dict:
    if len(self._portfolio_snapshots) < 2:
        return {"status": "insufficient data"}

    values = [s["portfolio_usd"] for s in self._portfolio_snapshots]
    returns = [
        (values[i + 1] - values[i]) / values[i]
        for i in range(len(values) - 1)
        if values[i] != 0
    ]

    total_return = (current - initial) / initial
    avg_return = sum(returns) / len(returns)
    std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5

    downside = [r for r in returns if r < 0]
    downside_std = (sum(r ** 2 for r in downside) / len(downside)) ** 0.5 if downside else 1e-9

    peak = initial
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    sharpe = (avg_return / std_return) * math.sqrt(len(returns)) if std_return > 0 else 0.0
    sortino = (avg_return / downside_std) * math.sqrt(len(returns)) if downside_std > 0 else 0.0
    calmar = total_return / max_dd if max_dd > 0 else 0.0

    return {
        "total_return_pct": round(total_return * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "calmar_ratio": round(calmar, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "num_snapshots": len(values),
    }
```

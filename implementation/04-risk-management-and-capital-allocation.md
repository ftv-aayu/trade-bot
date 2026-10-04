# 04 — Risk Management & Capital Allocation

This document describes the quantitative risk model, position sizing formula, portfolio allocation limits, and circuit breakers implemented in [`src/risk/manager.py`](file:///home/ayush/Practice/trade-bot/src/risk/manager.py) and [`src/main.py`](file:///home/ayush/Practice/trade-bot/src/main.py).

---

## 1. Risk Management Philosophy

Capital preservation always supersedes trade frequency. In algorithmic trading, the single greatest threat to long-term compounding is excessive risk per trade and fee drag. 

The risk subsystem enforces five layers of defense:
1. **Dynamic Volatility-Adjusted Sizing (Half-Kelly)**
2. **Hard Position & Portfolio Caps**
3. **Mandatory Liquidity Reserve (Cash Buffer)**
4. **Commission Efficiency Filters**
5. **Portfolio-Level Drawdown Circuit Breaker**

---

## 2. Dynamic Position Sizing (Half-Kelly Model)

The bot uses the **Kelly Criterion** scaled by half (Half-Kelly) to calculate optimal trade allocation while avoiding over-betting.

### 2.1 The Kelly Formula Derivation

The continuous Kelly fraction $f^*$ for a strategy with win rate $W$ and win/loss ratio $R$ is:
$$f^* = W - \frac{1 - W}{R}$$

Based on empirical backtests and live performance observations:
- Win Rate ($W$): $\approx 35\%$
- Win/Loss Payoff Ratio ($R$): $\approx 2.30$ (due to +3.0% take profit vs -1.3% average loss)

$$f^* = 0.35 - \frac{1 - 0.35}{2.30} = 0.35 - 0.2826 = 0.0674 \quad (6.74\%)$$

To maintain a conservative safety buffer against volatility clustering, the bot uses **Half-Kelly**:
$$\text{Base Fraction} = \frac{f^*}{2} \approx 3.4\% \quad (\text{BASE\_PCT} = 0.034)$$

On a $\$50,000$ portfolio, the base position size is:
$$\text{Base Size} = \$50,000 \times 0.034 = \$1,700$$

---

### 2.2 Signal Strength & Volatility Adjustments

The base allocation is dynamically scaled by two factors:

$$\text{Final Size} = \text{Base Size} \times \text{Signal Multiplier} \times \text{Volatility Multiplier}$$

#### 1. Signal Multiplier ($\text{signal\_mult} \in [0.75, 1.25]$):
Scaled by EMA separation and RSI closeness to the ideal 52 midpoint:
$$\text{Signal Strength} = \min\left(1.0, \, \text{EMA\_Sep\_Pct} \times 10 + \left(1 - \frac{|\text{RSI} - 52|}{15}\right)\right)$$
$$\text{Signal Multiplier} = 0.75 + (\text{Signal Strength} \times 0.50)$$

#### 2. Volatility Multiplier ($\text{vol\_mult} \in [0.40, 1.00]$):
Higher 24-hour price volatility reduces trade size to protect against erratic swings:
$$\text{Volatility Multiplier} = \max\left(0.40, \, \frac{1.0}{1.0 + (\text{Volatility \%} \times 0.30)}\right)$$

---

## 3. Hard Allocation & Portfolio Constraints

Even if the Kelly formula suggests a larger size, the risk manager strictly applies the following bounding limits:

| Parameter | Limit | Purpose |
|---|---|---|
| **Max Single Trade Cap** | `10.0%` of portfolio | Prevents oversized single allocations. |
| **Max Asset Exposure** | `15.0%` of portfolio | Limits cumulative coin exposure if accumulating. |
| **Max Concurrent Positions** | `6` positions | Ensures diversification across assets. |
| **Minimum Cash Reserve** | `20.0%` of starting balance | Keeps at least $\$10,000$ liquid cash for margin and new setups. |
| **Minimum Order Value** | `$\max(\text{MiniOrder}, \$1,000)$` | Prevents micro-orders where exchange commission eats into profits. |

---

## 4. Commission Drag Economics

On Roostoo, spot trades incur:
- **Taker Commission (Market Orders)**: $0.10\%$ ($0.001$)
- **Maker Commission (Limit Orders)**: $0.05\%$ ($0.0005$)

A round-trip market buy and sell costs **$0.20\%$** in direct fees, plus the bid-ask spread.

### Why Small Orders Destroy Returns:
- On a $\$100$ position, a $0.20\%$ fee is $\$0.20$. If minimum exchange precision or flat fee rounding applies, fees can exceed $1.0\%$ of the trade.
- To guarantee fee efficiency, the bot requires every buy order to satisfy:
  $$\text{Taker Fee USD} \le 0.15\% \text{ of Position Value}$$
  $$\text{Order Value} \ge \$1,000$$
  This automatically filters out ultra-low-priced meme tokens where wide spreads and precision truncation cause severe fee drag.

---

## 5. Proactive Position Rotation

When the maximum position cap (`MAX_POSITIONS = 6`) is reached, the bot does not simply stop trading. Instead, it evaluates whether to **rotate capital from a lagging position into a high-conviction setup**:

```
[New BUY Signal Detected & Max Positions (6/6) Reached]
   │
   ├─► 1. Score the new signal:
   │      new_score = (new_ema_sep * 10) + (1 - |new_rsi - 52| / 10)
   │
   ├─► 2. Scan all current open positions:
   │      Find the worst performer where Unrealized PnL is between -0.5% and -1.8%.
   │
   └─► 3. If a loser exists AND new_score > 0.5:
          ├── Sell the losing position at market.
          ├── Re-fetch free USD balance.
          └── Execute BUY on the stronger incoming pair.
```

This ensures capital is continuously concentrated in the strongest trending assets while systematically shedding weak performers.

---

## 6. Portfolio Drawdown Circuit Breaker

The risk manager continuously tracks the **High-Water Mark (Peak Equity)** of the portfolio:

$$\text{Peak Balance} = \max(\text{Peak Balance}, \, \text{Current Portfolio Value})$$
$$\text{Drawdown} = \frac{\text{Peak Balance} - \text{Current Value}}{\text{Peak Balance}}$$

If $\text{Drawdown} \ge 12.0\%$ (`max_drawdown_pct = 0.12`):
- `RiskManager._halted` is set to `True`.
- `RiskManager.position_size_usd()` returns `$0.00`.
- The bot halts all buying activity immediately.
- Positions are monitored and managed for orderly exits, but no new risk is taken until drawdown recovers.

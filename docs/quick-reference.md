# Quick Reference

## Current Strategy Settings

```python
# src/main.py
MomentumConfig(
    fast_ema        = 8,     # fast EMA window
    slow_ema        = 21,    # slow EMA window
    rsi_period      = 14,    # RSI lookback
    min_history     = 50,    # ticks needed before any signal

    rsi_buy_min     = 40.0,  # don't buy if RSI < 40 (already falling)
    rsi_buy_max     = 60.0,  # don't buy if RSI > 60 (already overbought)
    rsi_sell_min    = 45.0,  # don't sell on signal if RSI < 45 (bounce possible)

    ema_separation_pct = 0.02,  # crossover gap must be real, not noise

    min_hold_cycles = 3,     # hold at least 3 polls (15 min) before signal-sell
    min_profit_pct  = 0.25,  # must be +0.25% to sell on EMA signal
    stop_loss_pct   = 2.0,   # hard stop at -2%
    take_profit_pct = 3.0,   # auto-sell at +3%
)
```

## Signal Cheatsheet

```
RSI < 30   oversold     price fell too fast, likely bounce soon
RSI 30-40  weak         downtrend, avoid buying
RSI 40-60  neutral      healthy zone, good for entry
RSI 60-70  strong       momentum buying, getting crowded
RSI > 70   overbought   price rose too fast, likely pullback

EMA fast > slow   uptrend    short-term prices rising faster than long-term
EMA fast < slow   downtrend  short-term prices falling faster than long-term
EMA crossover ▲   bullish    trend just turned up  → potential BUY
EMA crossover ▼   bearish    trend just turned down → potential SELL
```

## Risk Numbers

```
Max per position:  15% of portfolio (~$7,500 on $50k)
Cash reserve:      20% always kept ($10,000 on $50k)
Stop-loss:         -2%  of entry price
Take-profit:       +3%  of entry price
Drawdown halt:     -12% from peak portfolio value
Commission:         0.1% per MARKET order (0.2% round-trip)
Break-even trade:  0.25% profit needed to cover fees + margin
```

## Poll & Timing

```
Poll interval:  5 minutes (300 seconds)
Warmup:         50 ticks × 5 min = ~4 hours (skipped if DB has data)
Min hold time:  3 cycles × 5 min = 15 minutes minimum
```

## Key Files

```
src/main.py              Main bot loop — config at top
src/strategy/momentum.py Strategy logic — EMA, RSI, signals
src/paper_trader.py      Fake trading engine — local balance
src/api/client.py        Roostoo API wrapper — real orders
src/risk/manager.py      Position sizing, drawdown halt
src/db.py                SQLite database read/write
docs/strategy.md         Full explanation of every concept
docs/alternative-strategies.md  Bollinger, MACD, Mean Reversion
```

## Database Queries

```bash
# Open DB
sqlite3 data/bot.sqlite3

# See all trades
SELECT datetime(timestamp_ms/1000,'unixepoch'), pair, side, quantity, price, fee
FROM trades ORDER BY timestamp_ms DESC LIMIT 20;

# Portfolio history
SELECT datetime(timestamp_ms/1000,'unixepoch'), equity
FROM equity ORDER BY timestamp_ms DESC LIMIT 20;

# Recent prices for BTC
SELECT datetime(timestamp_ms/1000,'unixepoch'), price, bid, ask
FROM prices WHERE pair='BTC/USD'
ORDER BY timestamp_ms DESC LIMIT 10;

# P&L summary
SELECT
  COUNT(*) as total_trades,
  SUM(CASE WHEN side='BUY' THEN 1 ELSE 0 END) as buys,
  SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) as sells,
  SUM(fee) as total_commission
FROM trades WHERE mode='paper';
```

## Switch Paper ↔ Live

In `src/main.py` line 33:
```python
PAPER_MODE = True   # paper trading (safe, no real orders)
PAPER_MODE = False  # live trading (real money)
```

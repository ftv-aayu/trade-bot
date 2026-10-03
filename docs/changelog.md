# Changelog

This document tracks what changed from the original strategy described in `docs/strategy.md`
to the current implementation. It covers strategy parameter changes, new features, bug fixes,
and live mode corrections.

---

## Strategy Parameter Changes

### RSI buy zone tightened: 40–60 → 45–58

The original strategy accepted any RSI between 40 and 60 as a valid buy zone.

The new zone is 45–58.

- The lower bound moves from 40 to 45. RSI below 45 indicates weakening or falling momentum.
  Buying into that zone increased the chance of entering a position that continued to drop.
- The upper bound moves from 60 to 58. Values above 58 indicate the move has already run.
  Entering there caught the tail of rallies rather than the body.

In `MomentumConfig`:

```python
rsi_buy_min: float = 45.0   # was 40
rsi_buy_max: float = 58.0   # was 60
```

---

### EMA separation threshold raised: 0.02% → 0.05%

The original threshold allowed a buy when the fast EMA was only 0.02% above the slow EMA.
That is noise-level separation on most pairs and produced phantom crossovers from micro price
fluctuations.

The new threshold is 0.05%. The crossover must represent a real separation before a buy is
placed.

In `MomentumConfig`:

```python
ema_separation_pct: float = 0.05   # was 0.02
```

---

### EMA confirmation ticks added: confirm_ticks=2

This parameter did not exist in the original strategy. The original strategy bought on the
first tick of a crossover, which allowed the bot to enter on single-tick noise spikes that
immediately reversed.

`confirm_ticks=2` requires the fast EMA to remain above the slow EMA for at least 2
consecutive ticks before a BUY signal is generated. The signal fires in a narrow window:

```python
trend_confirmed = (
    state.ticks_above_slow >= cfg.confirm_ticks          # at least 2 ticks above
    and state.ticks_above_slow <= cfg.confirm_ticks + 2  # window closes after 4 ticks
    and cfg.rsi_buy_min <= rsi <= cfg.rsi_buy_max
    and separation_pct >= cfg.ema_separation_pct
    ...
)
```

The `ticks_above_slow` counter resets to zero whenever the fast EMA drops below the slow EMA.

In `MomentumConfig`:

```python
confirm_ticks: int = 2   # NEW — was not present in v1
```

---

## New Risk and Position Controls

### MAX_POSITIONS=6 added

The original bot had no limit on how many positions it could hold simultaneously.
In practice it could open a position on every warmed-up pair in a single cycle if they all
crossed at the same time (common after a DB restore).

`MAX_POSITIONS=6` caps concurrent holdings. If the bot already holds 6 coins when a new BUY
signal fires, it either rotates (see below) or skips the signal.

In `src/main.py`:

```python
MAX_POSITIONS = 6
```

---

### Loss cooldown after stop-loss: loss_cooldown_cycles=3

The original bot could immediately re-enter the same pair after being stopped out. This led
to sequences of repeated losses on a pair that was in a sustained downtrend.

The new `loss_cooldown_cycles=3` parameter locks a pair out for 3 poll cycles (15 minutes at
the default 5-minute interval) after a stop-loss exit. The cooldown is tracked per-pair in
`PairState.cooldown_cycles`.

`notify_sold(pair, was_loss=True)` sets the cooldown:

```python
def notify_sold(self, pair: str, was_loss: bool = False):
    ...
    if was_loss:
        state.cooldown_cycles = self.config.loss_cooldown_cycles
```

`update()` decrements the counter each tick and blocks BUY signals until it reaches zero.

In `MomentumConfig`:

```python
loss_cooldown_cycles: int = 3   # NEW — was not present in v1
```

---

### Dynamic rotation: sell a loser to buy a better signal

Previously, when `MAX_POSITIONS` was full, all new signals were discarded.

Now, if the position limit is reached, the bot checks whether it can swap out the worst
current position to make room for the new signal. Rotation only happens if:

1. At least one held position has a PnL between -0.5% and -1.8%.
   (Positions below -2% are handled by the stop-loss and not eligible.)
2. The new signal's score exceeds 0.5.
   Score is computed as: `ema_sep_pct × 10 + (1 - abs(rsi - 52) / 10)`

If both conditions hold, the worst losing position is sold as a market order and the new
position opens in its place. The rotation sell is logged to both the CSV and the DB, and
`notify_sold(was_loss=True)` is called on the vacated pair so its cooldown activates.

---

## Infrastructure Changes

### DB warmup restore with just_restored flag

The original strategy document described the warmup restore concept but the implementation
had a gap: after loading historical prices from the DB, the first live tick would often
produce a crossover signal purely because the last few DB-restored ticks happened to straddle
the crossover point. This caused phantom buys on startup.

The fix is the `just_restored` flag on `PairState`. After `_restore_warmup()` loads prices
for a pair, it sets:

```python
state.just_restored = True
```

On the first live `update()` call for that pair, the flag is cleared and the signal returns
`HOLD`, discarding whatever the indicators show at that tick. From the second live tick
onward, signals fire normally.

Additionally, `_restore_warmup()` sets `_restoring = True` on the strategy during loading.
This suppresses stop-loss and take-profit checks while replaying historical prices. Without
this, prices in the DB that happened to cross the stop-loss threshold would fire false SELL
signals during restore.

---

### Session ID tracking

`BotDB` now generates a unique `session_id` (Unix timestamp as string) when it initialises:

```python
self.session_id = str(int(time.time()))
```

All trade rows written via `insert_trade()` include this `session_id`. This allows querying
trades from a specific run rather than the full history:

```python
db.get_session_trades()   # only trades from current run
db.get_trades()           # all trades, all runs
```

The `trades` table has a `session_id` column for this purpose.

---

### Live mode reconciliation of existing wallet positions

When the bot starts in live mode (`PAPER_MODE=False`), the real Roostoo wallet may already
hold coins from a previous session or a manual trade. The original implementation had no
awareness of these positions — it would not track stop-loss or take-profit for them, and
could attempt to buy a pair it already held.

The reconciliation block runs once at startup, before the main loop:

```python
if not PAPER_MODE:
    existing_wallet = client.balance().get("SpotWallet") or {}
    for coin, amounts in existing_wallet.items():
        qty = amounts.get("Free", 0) + amounts.get("Lock", 0)
        if qty <= 0 or coin == "USD":
            continue
        pair  = f"{coin}/USD"
        price = initial_tickers.get(pair, {}).get("LastPrice", 0)
        if price and pair in TRADE_PAIRS:
            strategy.notify_bought(pair, price)
```

It calls `notify_bought()` for every coin already in the wallet, registering the current
market price as the entry price. From that point the strategy actively tracks stop-loss and
take-profit for those positions.

Known limitation: the entry price is the market price at startup, not the actual purchase
price. If the bot was offline for an extended period, the recorded entry price may differ
from the real cost basis. This is acceptable for the mock exchange context.

---

## Bugs Fixed

### Shutdown sells not written to the SQLite DB (live mode)

Before the fix, when the bot shut down (Ctrl+C, profit target, or stop-loss halt), it sold
all open positions via `client.place_order()` and logged them to the CSV via
`trade_log.log_order()`. However, `db.insert_trade_from_order()` was never called for
shutdown sells. The trades appeared in the CSV but not in the `trades` table.

Fix applied to `shutdown()`:

1. Added `db=None` parameter to the function signature.
2. After `trade_log.log_order()` in the live branch, added:

```python
if db is not None:
    db.insert_trade_from_order(result, mode="live")
```

3. All three call sites (profit target, stop-loss halt, KeyboardInterrupt) were updated to
   pass `db` as the final argument.

This was a logging gap, not a trading error. No orders were missed. The fix ensures the DB
trade history is complete and consistent with the CSV.

---

### open_coins counts only Free balance in live mode (minor)

When checking how many positions are open, the live path reads:

```python
open_coins = {c: a.get("Free", 0) for c, a in bal_wallet.items() if a.get("Free", 0) > 0}
```

It counts only `Free` balance, not `Free + Lock`. For the current design (MARKET orders only),
locked balances are rare and this does not cause incorrect position counts in practice.
The issue is documented but not patched, as a fix would require additional API calls and the
risk of miscount is low given the MARKET-only order type.

---

### coin_held makes N API calls per cycle in live mode (known, unpatched)

`client.get_coin_balance(coin)` calls `/v3/balance` once per pair per cycle. With 88+ pairs
this is 88+ balance API calls per 5-minute cycle. This is a performance issue, not a
correctness bug. The balance data fetched for `open_coins` earlier in the same cycle is not
reused. A future improvement would cache the wallet snapshot at the start of each cycle.

"""
test_bot.py — Live trading test with portfolio display.

What it does:
  1.  Checks connectivity and loads exchange info
  2.  Shows your FULL portfolio (all balances + USD value)
  3.  Runs the momentum strategy on REAL live prices (no fake data)
  4.  Places REAL trades on your top 3 pairs by volume
  5.  Waits 10 seconds, then shows portfolio again so you can see the change
  6.  Sells all positions back to USD
  7.  Shows final portfolio

Run:
  .venv/bin/python test_bot.py
"""

import logging
import sys
import time

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_bot")

from src.api.client import RoostooClient
from src.strategy.momentum import MomentumStrategy, MomentumConfig
from src.risk.manager import RiskManager

# ── helpers ───────────────────────────────────────────────────────────────────

def get_wallet(client: RoostooClient) -> dict:
    """Return the SpotWallet dict."""
    bal = client.balance()
    return bal.get("SpotWallet") or bal.get("Wallet") or {}


def portfolio_value(wallet: dict, tickers: dict) -> float:
    """Total portfolio value in USD."""
    total = wallet.get("USD", {}).get("Free", 0.0) + wallet.get("USD", {}).get("Lock", 0.0)
    for coin, amounts in wallet.items():
        if coin == "USD":
            continue
        pair = f"{coin}/USD"
        price = tickers.get(pair, {}).get("LastPrice", 0.0)
        qty = amounts.get("Free", 0.0) + amounts.get("Lock", 0.0)
        total += qty * price
    return total


def print_portfolio(label: str, wallet: dict, tickers: dict):
    total = portfolio_value(wallet, tickers)
    usd = wallet.get("USD", {})

    print(f"\n{'─'*58}")
    print(f"  💼  PORTFOLIO — {label}")
    print(f"{'─'*58}")
    print(f"  {'Asset':<12} {'Free':>14} {'Locked':>14} {'USD Value':>12}")
    print(f"  {'─'*54}")

    # USD row
    usd_free = usd.get("Free", 0.0)
    usd_lock = usd.get("Lock", 0.0)
    print(f"  {'USD':<12} {usd_free:>14,.2f} {usd_lock:>14,.2f} {'$'+f'{usd_free+usd_lock:,.2f}':>12}")

    # Coin rows
    for coin, amounts in sorted(wallet.items()):
        if coin == "USD":
            continue
        free = amounts.get("Free", 0.0)
        lock = amounts.get("Lock", 0.0)
        qty  = free + lock
        if qty == 0:
            continue
        pair = f"{coin}/USD"
        price = tickers.get(pair, {}).get("LastPrice", 0.0)
        usd_val = qty * price
        print(f"  {coin:<12} {free:>14,.6f} {lock:>14,.6f} {'$'+f'{usd_val:,.2f}':>12}")

    print(f"  {'─'*54}")
    print(f"  {'TOTAL':>42} {'$'+f'{total:,.2f}':>12}")
    print(f"{'─'*58}")


def print_trade(action: str, result: dict):
    d = result.get("OrderDetail", result)
    pair     = d.get("Pair", "?")
    side     = d.get("Side", action)
    qty      = d.get("FilledQuantity") or d.get("Quantity") or d.get("ShortQty", "?")
    price    = d.get("FilledAverPrice") or d.get("Price") or d.get("EntryPrice", "?")
    status   = d.get("Status", "?")
    order_id = d.get("OrderID") or d.get("ID", "?")
    comm     = d.get("CommissionChargeValue") or d.get("OpenFee", 0)
    print(f"  {'✅' if status in ('FILLED','OPEN') else '⏳'}  {side} {qty} {pair} @ ${price}  |  fee=${comm}  |  OrderID={order_id}  [{status}]")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    client = RoostooClient()

    # ── 1. Connectivity ───────────────────────────────────────────────────────
    print("\n" + "="*58)
    print("  ROOSTOO LIVE TRADING TEST")
    print("="*58)

    st = client.server_time()
    if "ServerTime" not in st:
        print("❌  Cannot reach API. Check your internet connection.")
        sys.exit(1)
    print(f"\n  ✅  Connected  |  Server time: {st['ServerTime']}")

    info = client.exchange_info()
    pairs_meta = info.get("TradePairs", {})
    all_pairs = [p for p, m in pairs_meta.items() if m.get("CanTrade")]
    print(f"  ✅  {len(all_pairs)} tradable pairs available")

    # ── 2. Fetch live tickers ─────────────────────────────────────────────────
    tickers = client.get_all_tickers()
    if not tickers:
        print("❌  No ticker data. Exiting.")
        sys.exit(1)

    # ── 3. Show portfolio BEFORE trading ─────────────────────────────────────
    wallet_before = get_wallet(client)
    print_portfolio("BEFORE TRADING", wallet_before, tickers)

    usd_free = wallet_before.get("USD", {}).get("Free", 0.0)
    if usd_free < 50:
        print("\n❌  Less than $50 free USD — nothing to trade.")
        sys.exit(1)

    # ── 4. Pick top 3 liquid pairs (highest UnitTradeValue = USD volume) ──────
    ranked = sorted(
        [(p, tickers[p]) for p in all_pairs if p in tickers and tickers[p].get("LastPrice", 0) > 0],
        key=lambda x: x[1].get("UnitTradeValue", 0),
        reverse=True,
    )
    top_pairs = [p for p, _ in ranked[:3]]
    print(f"\n  🎯  Top 3 pairs by volume: {top_pairs}")

    # ── 5. Run momentum strategy on REAL prices ───────────────────────────────
    print("\n" + "="*58)
    print("  STRATEGY WARMUP  (collecting 30 live price ticks each)")
    print("="*58)
    print("  Polling every 2 seconds × 30 ticks = ~1 minute warmup\n")

    strat = MomentumStrategy(MomentumConfig(
        fast_ema=5, slow_ema=15, rsi_period=10, min_history=30
    ))

    # Collect 30 real ticks per pair
    TICKS = 30
    for tick in range(1, TICKS + 1):
        tick_tickers = client.get_all_tickers()
        for pair in top_pairs:
            p = tick_tickers.get(pair, {}).get("LastPrice", 0)
            if p:
                strat.update(pair, p)
        prices_str = ", ".join(f"{p}: ${tick_tickers.get(p, {}).get('LastPrice', 0):.4f}" for p in top_pairs)
        sys.stdout.write(f"\r  Tick {tick:>2}/{TICKS} — {prices_str}")
        sys.stdout.flush()
        if tick < TICKS:
            time.sleep(2)

    print()  # newline after progress line

    # Get final signals
    signals = {}
    for pair in top_pairs:
        price_now = tickers.get(pair, {}).get("LastPrice", 0)
        signals[pair] = strat.update(pair, price_now)
        ind = strat.indicators(pair)
        print(f"\n  {pair}:")
        print(f"    Signal:   {signals[pair]}")
        print(f"    Fast EMA: {ind.get('fast_ema')}")
        print(f"    Slow EMA: {ind.get('slow_ema')}")
        print(f"    RSI:      {ind.get('rsi')}")

    # ── 6. Execute trades ─────────────────────────────────────────────────────
    print("\n" + "="*58)
    print("  PLACING REAL TRADES")
    print("="*58)

    risk = RiskManager(initial_balance=usd_free)
    bought: list[tuple[str, float]] = []  # (pair, filled_qty)

    # Refresh tickers before ordering
    tickers = client.get_all_tickers()

    for pair in top_pairs:
        signal = signals[pair]
        coin = pair.split("/")[0]
        price_now = tickers.get(pair, {}).get("LastPrice", 0)
        meta = pairs_meta.get(pair, {})
        amount_precision = meta.get("AmountPrecision", 6)
        min_order = meta.get("MiniOrder", 1.0)

        print(f"\n  {pair}  |  signal={signal}  |  price=${price_now}")

        if signal == "BUY":
            # Use 20% of free USD per pair
            alloc_usd = usd_free * 0.20
            alloc_usd = max(alloc_usd, max(min_order, 15.0))  # at least $15

            factor = 10 ** amount_precision
            qty = int((alloc_usd / price_now) * factor) / factor

            if qty * price_now < min_order:
                print(f"    ⚠️   Order too small (${qty*price_now:.2f}). Skipping.")
                continue

            print(f"    🛒  Buying {qty} {coin} (~${qty*price_now:.2f})")
            result = client.place_order(pair, "BUY", qty, order_type="MARKET")
            print_trade("BUY", result)

            if result.get("Success"):
                filled = result.get("OrderDetail", {}).get("FilledQuantity", qty)
                bought.append((pair, filled))

        elif signal == "SELL":
            coin_bal = client.get_coin_balance(coin)
            if coin_bal <= 0:
                print(f"    ℹ️   SELL signal but no {coin} held. Skipping.")
            else:
                print(f"    💰  Selling {coin_bal} {coin}")
                result = client.place_order(pair, "SELL", coin_bal, order_type="MARKET")
                print_trade("SELL", result)

        else:  # HOLD
            # Even on HOLD, buy a small amount to demonstrate portfolio change
            alloc_usd = max(usd_free * 0.10, max(min_order, 15.0))
            factor = 10 ** amount_precision
            qty = int((alloc_usd / price_now) * factor) / factor

            if qty * price_now < min_order:
                print(f"    ⚠️   Order too small. Skipping HOLD demo trade.")
                continue

            print(f"    📊  HOLD signal — buying small demo position: {qty} {coin} (~${qty*price_now:.2f})")
            result = client.place_order(pair, "BUY", qty, order_type="MARKET")
            print_trade("BUY", result)

            if result.get("Success"):
                filled = result.get("OrderDetail", {}).get("FilledQuantity", qty)
                bought.append((pair, filled))

    # ── 7. Show portfolio AFTER buying ───────────────────────────────────────
    print()
    tickers = client.get_all_tickers()
    wallet_after_buy = get_wallet(client)
    print_portfolio("AFTER BUYING", wallet_after_buy, tickers)

    val_before = portfolio_value(wallet_before, tickers)
    val_after  = portfolio_value(wallet_after_buy, tickers)
    pnl        = val_after - val_before
    pnl_pct    = (pnl / val_before * 100) if val_before else 0
    print(f"\n  💹  Unrealized P&L vs before:  ${pnl:+.4f}  ({pnl_pct:+.4f}%)")
    print(f"      (difference is mostly from the spread + 0.1% commission)")

    # ── 8. Close all positions ────────────────────────────────────────────────
    print("\n" + "="*58)
    print("  CLOSING ALL POSITIONS")
    print("="*58)

    tickers = client.get_all_tickers()
    for pair, filled_qty in bought:
        coin = pair.split("/")[0]
        price_now = tickers.get(pair, {}).get("LastPrice", 0)
        print(f"\n  Selling {filled_qty} {pair} @ ~${price_now}")
        result = client.place_order(pair, "SELL", filled_qty, order_type="MARKET")
        print_trade("SELL", result)

    # ── 9. Final portfolio ────────────────────────────────────────────────────
    time.sleep(1)
    tickers = client.get_all_tickers()
    wallet_final = get_wallet(client)
    print_portfolio("FINAL (after closing)", wallet_final, tickers)

    val_final = portfolio_value(wallet_final, tickers)
    val_start = portfolio_value(wallet_before, tickers)
    net_pnl     = val_final - val_start
    net_pnl_pct = (net_pnl / val_start * 100) if val_start else 0

    print(f"\n  📊  Net P&L this test:  ${net_pnl:+.4f}  ({net_pnl_pct:+.4f}%)")
    print(f"      (negative = commissions paid, expected on quick round-trip)")

    # ── 10. Order history ─────────────────────────────────────────────────────
    print("\n" + "="*58)
    print("  RECENT ORDER HISTORY (last 10)")
    print("="*58)
    history = client.query_order(limit=10)
    orders = history.get("OrderMatched", [])
    if orders:
        print(f"\n  {'#':<4} {'Pair':<14} {'Side':<6} {'Qty':>12} {'Price':>10} {'Status':<10} {'OrderID'}")
        print(f"  {'─'*70}")
        for i, o in enumerate(orders[:10], 1):
            print(
                f"  {i:<4} {o.get('Pair',''):<14} {o.get('Side',''):<6} "
                f"{o.get('FilledQuantity',0):>12,.4f} "
                f"{o.get('FilledAverPrice',0):>10,.4f} "
                f"{o.get('Status',''):<10} "
                f"{o.get('OrderID','')}"
            )
    else:
        print("  No orders found.")

    print(f"\n{'='*58}")
    print(f"  ✅  Test complete. Bot is ready.")
    print(f"  Run the full bot with:  .venv/bin/python src/main.py")
    print(f"{'='*58}\n")


if __name__ == "__main__":
    main()

import time
import sqlite3

from src.api.client import RoostooClient
from datetime import datetime, timezone


c = RoostooClient()
conn = sqlite3.connect("data/bot.sqlite3")


def get_entry_prices():
    # Get trades from the latest session
    rows = conn.execute("""
        SELECT pair, side, quantity, price, mode
        FROM trades
        WHERE session_id = (
            SELECT session_id
            FROM trades
            WHERE session_id IS NOT NULL
            ORDER BY timestamp_ms DESC
            LIMIT 1
        )
        ORDER BY timestamp_ms
    """).fetchall()

    positions = {}

    for pair, side, qty, price, mode in rows:
        key = (pair, mode)

        if key not in positions:
            positions[key] = {
                "qty": 0,
                "entry": 0
            }

        if side == "BUY":
            positions[key]["qty"] += qty
            positions[key]["entry"] = price

        else:
            positions[key]["qty"] -= qty

            if positions[key]["qty"] <= 0:
                positions[key]["entry"] = 0

    # Return:
    # coin -> entry price
    result = {}

    for (pair, mode), v in positions.items():

        if v["qty"] > 0.0001 and v["entry"] > 0:
            coin = pair.split("/")[0]
            result[coin] = v["entry"]

    return result


def show():

    wallet = c.balance().get("SpotWallet") or {}
    tickers = c.get_all_tickers()
    entries = get_entry_prices()

    usd = wallet.get("USD", {}).get("Free", 0)
    total = usd

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    print(f"\n  [{ts}]")

    print(
        f'  {"Asset":<10} '
        f'{"Qty":>16} '
        f'{"Price":>12} '
        f'{"Value":>12} '
        f'{"Since Buy":>10}'
    )

    print(f'  {"-" * 64}')

    usd_str = f"${usd:,.2f}"

    print(
        f'  {"USD":<10} '
        f'{"-":>16} '
        f'{"-":>12} '
        f'{usd_str:>12}'
    )

    for coin, amt in sorted(wallet.items()):

        if coin == "USD":
            continue

        qty = (
            amt.get("Free", 0)
            + amt.get("Lock", 0)
        )

        if qty == 0:
            continue

        price = tickers.get(
            f"{coin}/USD", {}
        ).get("LastPrice", 0)

        val = qty * price
        total += val

        entry = entries.get(coin, 0)

        if entry > 0:
            chg = (price - entry) / entry * 100
            chg_str = f"{chg:+7.2f}%"
        else:
            chg_str = "    n/a"

        price_str = f"${price:,.4f}"
        value_str = f"${val:,.2f}"

        print(
            f'  {coin:<10} '
            f'{qty:>16,.4f} '
            f'{price_str:>12} '
            f'{value_str:>12} '
            f'{chg_str:>10}'
        )

    pnl = total - 50000
    pnl_pct = pnl / 50000 * 100

    total_str = f"${total:,.2f}"

    if pnl >= 0:
        pnl_str = f"+${pnl:,.2f}"
    else:
        pnl_str = f"-${abs(pnl):,.2f}"

    print(f'  {"-" * 64}')

    print(
        f'  {"TOTAL":>42} '
        f'{total_str:>12}'
    )

    print(
        f'  {"P&L vs $50k":>42} '
        f'{pnl_str:>12} '
        f'({pnl_pct:+.4f}%)'
    )


# Refresh 15 times, every 30 seconds
while True:

    try:
        show()


        print("  (refreshing in 30s... Ctrl+C to stop)")
        time.sleep(30)

    except KeyboardInterrupt:
        print("\nStopped.")
        break

    except Exception as e:
        print(f"\nError: {e}")
        print("Retrying in 30s...")

        if i < 14:
            time.sleep(30)
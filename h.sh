cd /home/mai/Documents/trade-bot && .venv/bin/python -c "
import time, sqlite3
from src.api.client import RoostooClient
from datetime import datetime, timezone

c    = RoostooClient()
conn = sqlite3.connect('data/bot.sqlite3')

def get_entry_prices():
    # Get the latest session_id for each mode
    rows = conn.execute('''
        SELECT pair, side, quantity, price, mode
        FROM trades
        WHERE session_id IN (
            SELECT session_id FROM trades
            WHERE session_id IS NOT NULL
            GROUP BY session_id
        )
        ORDER BY timestamp_ms
    ''').fetchall()

    positions = {}
    for pair, side, qty, price, mode in rows:
        key = (pair, mode)
        if key not in positions:
            positions[key] = {'qty': 0, 'entry': 0}
        if side == 'BUY':
            positions[key]['qty']   += qty
            positions[key]['entry']  = price
        else:
            positions[key]['qty'] -= qty
            if positions[key]['qty'] <= 0:
                positions[key]['entry'] = 0

    # Return flat dict: coin -> entry_price (if still held)
    result = {}
    for (pair, mode), v in positions.items():
        if v['qty'] > 0.0001 and v['entry'] > 0:
            coin = pair.split('/')[0]
            result[coin] = v['entry']
    return result

def show():
    wallet   = c.balance().get('SpotWallet') or {}
    tickers  = c.get_all_tickers()
    entries  = get_entry_prices()

    usd   = wallet.get('USD', {}).get('Free', 0)
    total = usd

    ts = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
    print(f'\n  [{ts}]')
    print(f'  {\"Asset\":<10} {\"Qty\":>16} {\"Price\":>12} {\"Value\":>12} {\"Since Buy\":>10}')
    print(f'  {\"-\"*64}')
    print(f'  {\"USD\":<10} {\"-\":>16} {\"-\":>12} {\"\$\"+f\"{usd:,.2f}\":>12}')

    for coin, amt in sorted(wallet.items()):
        if coin == \"USD\": continue
        qty   = amt.get(\"Free\", 0) + amt.get(\"Lock\", 0)
        if qty == 0: continue
        price = tickers.get(f\"{coin}/USD\", {}).get(\"LastPrice\", 0)
        val   = qty * price
        total += val

        entry = entries.get(coin, 0)
        if entry > 0:
            chg     = (price - entry) / entry * 100
            chg_str = f\"{chg:>+7.2f}%\"
        else:
            chg_str = \"    n/a\"

        print(f'  {coin:<10} {qty:>16,.4f} {\"\$\"+f\"{price:,.4f}\":>12} {\"\$\"+f\"{val:,.2f}\":>12} {chg_str:>10}')

    pnl     = total - 50000
    pnl_pct = pnl / 50000 * 100
    print(f'  {\"-\"*64}')
    print(f'  {\"TOTAL\":>42} {\"\$\"+f\"{total:,.2f}\":>12}')
    print(f'  {\"P&L vs \$50k\":>42} {\"+\$\"+f\"{pnl:,.2f}\" if pnl>=0 else \"-\$\"+f\"{abs(pnl):,.2f}\":>12}  ({pnl_pct:+.4f}%)')

for i in range(15):
    show()
    if i < 14:
        print(f'  (refreshing in 30s... Ctrl+C to stop)')
        time.sleep(30)
" 2>&1

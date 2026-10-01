cd /home/mai/Documents/trade-bot && .venv/bin/python -c "
import time
from src.api.client import RoostooClient
from datetime import datetime, timezone

c = RoostooClient()

def show():
	wallet  = c.balance().get('SpotWallet') or {}
	tickers = c.get_all_tickers()

	usd  = wallet.get('USD', {}).get('Free', 0)
	total = usd

	ts = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
	print(f'\n  [{ts}]')
	print(f'  {\"Asset\":<10} {\"Qty\":>18} {\"Price\":>12} {\"Value\":>12} {\"24h\":>8}')
	print(f'  {\"-\"*64}')
	print(f'  {\"USD\":<10} {\"-\":>18} {\"-\":>12} {\"\$\"+f\"{usd:,.2f}\":>12}')

	for coin, amt in sorted(wallet.items()):
		if coin == \"USD\": continue
		qty = amt.get(\"Free\", 0) + amt.get(\"Lock\", 0)
		if qty == 0: continue
		ticker = tickers.get(f\"{coin}/USD\", {})
		price  = ticker.get(\"LastPrice\", 0)
		chg    = ticker.get(\"Change\", 0) * 100
		val    = qty * price
		total += val
		print(f'  {coin:<10} {qty:>18,.4f} {\"\$\"+f\"{price:,.4f}\":>12}{\"\$\"+f\"{val:,.2f}\":>12} {chg:>+7.2f}%')

	pnl     = total - 50000
	pnl_pct = pnl / 50000 * 100
	print(f'  {\"-\"*64}')
	print(f'  {\"TOTAL\":>44} {\"\$\"+f\"{total:,.2f}\":>12}')
	print(f'  {\"P&L vs \$50k\":>44} {\"+\$\"+f\"{pnl:,.2f}\" if pnl>=0 else
\"-\$\"+f\"{abs(pnl):,.2f}\":>12}  ({pnl_pct:+.4f}%)')

for i in range(15):
	show()
	if i < 4:
		print(f'  (refreshing in 30s... Ctrl+C to stop)')
	time.sleep(30)
" 2>&1

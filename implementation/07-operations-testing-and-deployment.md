# 07 — Operations, Testing & Deployment

This runbook covers configuring the bot, running diagnostics, switching between Paper and Live modes, and deploying to AWS EC2 or Linux servers.

---

## 1. Environment Configuration

The bot expects API credentials to be loaded through environment variables or a local `.env` file in the project root.

### Setup `.env`:
Create a `.env` file in the root directory:
```bash
ROOSTOO_API_KEY="your_api_key_here"
ROOSTOO_API_SECRET="your_api_secret_here"
```

> [!CAUTION]
> Never commit `.env` or API credentials to Git. Ensure `.env` is listed in `.gitignore`.

---

## 2. Paper Mode vs. Live Mode Switch

Configuration flags are located near the top of [`src/main.py`](file:///home/ayush/Practice/trade-bot/src/main.py#L62-L72):

```python
# ===============================================================
#  CONFIGURATION -- edit these before running
# ===============================================================
PAPER_MODE         = False     # True = fake local balance | False = real exchange trades
STARTING_BALANCE   = 50_000.0  # Used for risk sizing (in live mode, auto-set from real wallet)
POLL_INTERVAL      = 300       # 5 minutes (300s)
PERF_INTERVAL      = 900       # Performance printout interval (15 min)
PROFIT_TARGET_PCT  = 1.7       # Auto-exit when profit hits +1.7% (or None to run continuously)
STOP_LOSS_PCT      = 4.57      # Portfolio stop-loss halt
MAX_POSITIONS      = 6         # Maximum concurrent open coin positions
# ===============================================================
```

- **When `PAPER_MODE = True`**: The bot fetches real prices, but orders are intercepted by [`PaperTrader`](file:///home/ayush/Practice/trade-bot/src/paper_trader.py). No real orders are sent to the exchange.
- **When `PAPER_MODE = False`**: Real market orders are placed on your Roostoo account via [`RoostooClient`](file:///home/ayush/Practice/trade-bot/src/api/client.py).

---

## 3. Diagnostic & Testing Tools

### 3.1 End-to-End Integration Test (`test_bot.py`)
To verify API connectivity, account balances, strategy warmup, and test order execution in a single run:
```bash
python test_bot.py
```

What `test_bot.py` does:
1. Verifies API credentials against `/v3/serverTime` and `/v3/exchangeInfo`.
2. Prints full SpotWallet balances.
3. Identifies the top 3 pairs by 24h volume.
4. Performs a fast 30-tick warmup (2-second interval).
5. Executes a test BUY order on each pair.
6. Displays the updated portfolio with unrealized P&L.
7. Executes a SELL order to close positions back to USD.
8. Displays net P&L and recent order history from `/v3/query_order`.

---

### 3.2 Real-Time Portfolio Monitor (`h.py`)
To inspect your open positions, entry prices, live market prices, and unrealized returns without restarting the bot:
```bash
python h.py
```
This utility queries SQLite (`data/bot.sqlite3`) for original entry prices and merges them with live tickers from Roostoo, printing a refreshed P&L table every 30 seconds.

---

## 4. Production Deployment on AWS EC2 / Linux

### 4.1 Server Setup (Ubuntu 22.04 LTS recommended)
```bash
# 1. Update system packages
sudo apt update && sudo apt install -y python3 python3-pip python3-venv screen git sqlite3

# 2. Clone repository
git clone https://github.com/jnv-memories/trade-bot.git
cd trade-bot

# 3. Create virtual environment & install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Set credentials
nano .env
```

---

### 4.2 Running with `screen` (Simple & Persistent)

To ensure the bot continues running after closing your SSH terminal:

```bash
# Start a new screen session named 'tradebot'
screen -S tradebot

# Activate virtualenv and run the bot
source .venv/bin/activate
python src/main.py

# Detach from session: Press Ctrl+A, then press D
```

#### Re-attaching to the session later:
```bash
# List running screens
screen -ls

# Re-attach
screen -r tradebot
```

---

### 4.3 Running as a `systemd` Service (Production Auto-Restart)

To run the bot as a background Linux daemon with automatic restart on server reboots or unexpected crashes:

Create service file:
```bash
sudo nano /etc/systemd/system/tradebot.service
```

Paste configuration (adjust paths and user accordingly):
```ini
[Unit]
Description=Roostoo Autonomous Trade Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trade-bot
EnvironmentFile=/home/ubuntu/trade-bot/.env
ExecStart=/home/ubuntu/trade-bot/.venv/bin/python /home/ubuntu/trade-bot/src/main.py
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/trade-bot/logs/systemd.log
StandardError=append:/home/ubuntu/trade-bot/logs/systemd.log

[Install]
WantedBy=multi-user.target
```

Enable and start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable tradebot
sudo systemctl start tradebot

# Check status
sudo systemctl status tradebot

# Follow live logs
tail -f logs/bot.log
```

---

## 5. Troubleshooting & Error Recovery

| Issue / Symptom | Root Cause | Resolution |
|---|---|---|
| `[FAIL] Cannot reach API` | Incorrect API keys, missing `.env`, or internet connectivity failure. | Check `.env` file; test connectivity with `curl -s https://mock-api.roostoo.com/v3/serverTime`. |
| `insufficient paper balance` / `size_usd <= 0` | Free USD is below the 20% cash reserve limit or order size is less than `$10`. | Deposit funds or lower `reserve_pct` in `RiskConfig`. |
| `SKIP SELL: coin_held but Free=0` | Coins are currently locked in a pending limit order. | Cancel pending orders via `client.cancel_order()` or wait for settlement. |
| `DRAWDOWN HALT` | Portfolio equity has dropped more than 12% below its peak high-water mark. | The circuit breaker is active. Inspect open positions, wait for recovery, or restart with adjusted baseline. |
| Database lock error | SQLite connection concurrency collision. | Ensure WAL mode is active (`PRAGMA journal_mode=WAL`). Use thread-local connections as provided by `BotDB`. |

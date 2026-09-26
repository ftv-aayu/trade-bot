# Trade Bot

An autonomous trading bot that interacts with the Roostoo mock exchange via REST API.

The bot makes buy, hold, and sell decisions automatically to maximize portfolio returns while managing risk, evaluated by:
- Portfolio Return
- Sortino Ratio
- Sharpe Ratio
- Calmar Ratio

## Overview

The bot connects to the Roostoo backend exchange engine using POST and GET API requests, executes spot trades (1x long/short, no leverage), and logs all activity internally for performance tracking.

## Features

- Autonomous trade execution — no manual intervention
- Real-time market data ingestion via Roostoo API
- Portfolio and risk management built-in
- Internal trade and performance logging
- Deployable on AWS EC2

## Project Structure

```
trade-bot/
├── src/
│   ├── api/          # Roostoo API client (GET/POST wrappers)
│   ├── strategy/     # Trading strategy logic
│   ├── risk/         # Risk management and position sizing
│   └── logger/       # Trade and performance logging
├── config/           # Configuration (API keys via env vars)
├── logs/             # Runtime trade and performance logs
├── tests/            # Unit and integration tests
├── plan.md           # Development roadmap
├── requirements.txt  # Python dependencies
└── README.md
```

## Setup

### Prerequisites
- Python 3.10+
- AWS EC2 instance (Ubuntu recommended)
- Roostoo API credentials (set as environment variables)

### Installation

```bash
git clone https://github.com/jnv-memories/trade-bot.git
cd trade-bot
pip install -r requirements.txt
```

### Configuration

Set your credentials as environment variables — never hardcode them:

```bash
export ROOSTOO_API_KEY=your_api_key
export ROOSTOO_API_SECRET=your_api_secret
```

### Running the Bot

```bash
python src/main.py
```

## Deployment (AWS EC2)

1. Launch an EC2 instance (Ubuntu 22.04 recommended, t2.micro or higher)
2. SSH into the instance and clone this repo
3. Install dependencies and set environment variables
4. Run the bot in a persistent session:

```bash
screen -S tradebot
python src/main.py
# Detach with Ctrl+A, D
```

## Rules and Constraints

- No high-frequency trading, market-making, or arbitrage
- Spot trading only (1x), no leverage
- Starting portfolio: $100,000 mock USD
- Commission: 0.1% taker (market order), 0.05% maker (limit order)

## License

Open source — submitted for code validation as per competition requirements.

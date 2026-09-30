"""
Main Bot Loop
Polls the market every POLL_INTERVAL seconds, runs the momentum strategy,
and executes trades within risk limits.
"""

import logging
import time
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Configure logging before importing anything else
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log"),
    ],
)
logger = logging.getLogger("main")

from src.api.client import RoostooClient
from src.strategy.momentum import MomentumStrategy, MomentumConfig
from src.risk.manager import RiskManager, RiskConfig
from src.logger.trade_logger import TradeLogger

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
POLL_INTERVAL = 30          # seconds between each market poll
PERF_LOG_INTERVAL = 300     # seconds between performance summaries
INITIAL_BALANCE = 100_000.0 # competition starting USD

# Pairs to trade — fetch from exchange info at startup
TRADE_PAIRS: list[str] = []

# Track which pairs we currently hold (long)
HOLDINGS: dict[str, float] = {}  # pair -> coin quantity held


def estimate_portfolio_value(client: RoostooClient) -> float:
    """Estimate total portfolio value in USD."""
    try:
        bal = client.balance()
        wallet = bal.get("SpotWallet") or bal.get("Wallet", {})
        total = wallet.get("USD", {}).get("Free", 0.0) + wallet.get("USD", {}).get("Lock", 0.0)

        # Add value of coin holdings
        tickers = client.get_all_tickers()
        for coin, amounts in wallet.items():
            if coin == "USD":
                continue
            pair = f"{coin}/USD"
            price = tickers.get(pair, {}).get("LastPrice", 0.0)
            coin_qty = amounts.get("Free", 0.0) + amounts.get("Lock", 0.0)
            total += coin_qty * price

        return total
    except Exception as e:
        logger.error("Could not estimate portfolio value: %s", e)
        return INITIAL_BALANCE


def get_exchange_pairs(client: RoostooClient) -> list[str]:
    """Fetch all tradable pairs from the exchange."""
    info = client.exchange_info()
    pairs = [
        p for p, meta in info.get("TradePairs", {}).items()
        if meta.get("CanTrade", False)
    ]
    logger.info("Tradable pairs: %s", pairs)
    return pairs


def get_amount_precision(exchange_info: dict, pair: str) -> int:
    return exchange_info.get("TradePairs", {}).get(pair, {}).get("AmountPrecision", 6)


def run():
    logger.info("=== Trade Bot Starting ===")

    client = RoostooClient()

    # Verify connectivity
    st = client.server_time()
    logger.info("Server time: %s", st.get("ServerTime"))

    # Load exchange metadata
    exchange_info = client.exchange_info()
    global TRADE_PAIRS
    TRADE_PAIRS = get_exchange_pairs(client)
    if not TRADE_PAIRS:
        logger.error("No tradable pairs found. Exiting.")
        return

    # Init components
    strategy = MomentumStrategy(MomentumConfig(fast_ema=5, slow_ema=20, rsi_period=14))
    risk = RiskManager(initial_balance=INITIAL_BALANCE)
    trade_log = TradeLogger()

    last_perf_log = time.time()
    cycle = 0

    logger.info("Bot running. Polling every %ds. Press Ctrl+C to stop.", POLL_INTERVAL)

    while True:
        try:
            cycle += 1
            logger.debug("--- Cycle %d ---", cycle)

            # 1. Get portfolio value and update risk manager
            portfolio_value = estimate_portfolio_value(client)
            risk.update_balance(portfolio_value)
            trade_log.snapshot(portfolio_value)

            if risk.is_halted():
                logger.warning("Trading halted due to drawdown. Monitoring only.")
                time.sleep(POLL_INTERVAL)
                continue

            # 2. Get free USD balance
            usd_free = client.get_usd_balance()

            # 3. Fetch tickers for all pairs
            all_tickers = client.get_all_tickers()
            if not all_tickers:
                logger.warning("Empty ticker response. Skipping cycle.")
                time.sleep(POLL_INTERVAL)
                continue

            # 4. Run strategy for each pair
            for pair in TRADE_PAIRS:
                ticker = all_tickers.get(pair)
                if not ticker:
                    continue

                price = ticker.get("LastPrice", 0.0)
                if not price:
                    continue

                signal = strategy.update(pair, price)
                coin = pair.split("/")[0]
                coin_held = client.get_coin_balance(coin)
                amount_precision = get_amount_precision(exchange_info, pair)

                if signal == "BUY" and coin_held == 0:
                    # Size the position
                    size_usd = risk.position_size_usd(usd_free, portfolio_value)
                    if size_usd <= 0:
                        continue
                    qty = risk.quantity_for_usd(size_usd, price, amount_precision)
                    if not risk.check_order(qty, price):
                        continue

                    logger.info("BUY signal: %s qty=%.6f @ %.4f", pair, qty, price)
                    result = client.place_order(pair, "BUY", qty, order_type="MARKET")
                    if result.get("Success"):
                        trade_log.log_order(result, note="momentum_buy")
                        usd_free -= size_usd  # optimistic local deduction

                elif signal == "SELL" and coin_held > 0:
                    # Sell all held coins
                    qty = coin_held
                    if not risk.check_order(qty, price):
                        continue

                    logger.info("SELL signal: %s qty=%.6f @ %.4f", pair, qty, price)
                    result = client.place_order(pair, "SELL", qty, order_type="MARKET")
                    if result.get("Success"):
                        trade_log.log_order(result, note="momentum_sell")

            # 5. Periodic performance summary
            if time.time() - last_perf_log >= PERF_LOG_INTERVAL:
                trade_log.performance_summary(INITIAL_BALANCE, portfolio_value)
                last_perf_log = time.time()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Stopping bot...")
            trade_log.performance_summary(INITIAL_BALANCE, estimate_portfolio_value(client))
            break
        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()

"""trade-bot main polling loop.

Fetches public market data from Binance (crypto) and Yahoo Finance (stocks),
runs the ML predictor and signal scoring engine on each universe independently,
persists results to SQLite, and logs periodic summaries.

No live trading. No API keys required.

Run with:
    python -m src
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(override=False)

from .api.client import BinanceClient
from .api.stock_client import StockClient, is_market_open, minutes_until_open
from .config import Config
from .db import BotDB
from .logger.trade_logger import TradeLogger
from .strategy.boss import BossStrategy, Market

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("trade_bot")


def setup_logging(log_path: str) -> None:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(fmt)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stdout_handler)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)


def now_ms() -> int:
    return int(time.time() * 1000)


def now_label() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


# ---------------------------------------------------------------------------
# Crypto prefetch
# ---------------------------------------------------------------------------

def prefetch_crypto(client: BinanceClient, db: BotDB, symbols: list[str],
                    cfg: Config, target_bars: int = 3000) -> None:
    """Download historical klines from Binance to warm up the predictor."""
    total = len(symbols)
    logger.info("Crypto prefetch: up to %d bars × %d symbols (%s)...",
                target_bars, total, cfg.kline_interval)
    for idx, symbol in enumerate(symbols):
        already = db.history_counts([symbol]).get(symbol, 0)
        if already >= target_bars:
            logger.debug("[%d/%d] %s already has %d bars, skipping", idx+1, total, symbol, already)
            continue
        try:
            all_klines: list[tuple[int, float]] = []
            end_time_ms: int | None = None
            requests_made = 0
            while len(all_klines) < target_bars:
                remaining = target_bars - len(all_klines)
                raw = client.klines(symbol, interval=cfg.kline_interval,
                                    limit=min(1000, remaining), end_time_ms=end_time_ms)
                if not raw:
                    break
                batch = [(int(k[0]), float(k[4])) for k in raw if len(k) > 4 and float(k[4]) > 0]
                if not batch:
                    break
                all_klines = batch + all_klines
                requests_made += 1
                end_time_ms = batch[0][0] - 1
                time.sleep(0.12)
                if len(raw) < min(1000, remaining):
                    break
            if all_klines:
                db.add_kline_prices(symbol, all_klines, asset_type="crypto")
                logger.info("[%d/%d] %s: %d bars (%d requests)",
                            idx+1, total, symbol, len(all_klines), requests_made)
            else:
                logger.warning("[%d/%d] %s: no data", idx+1, total, symbol)
        except Exception as exc:
            logger.error("[%d/%d] %s: prefetch error: %s", idx+1, total, symbol, exc)
        time.sleep(0.12)


# ---------------------------------------------------------------------------
# Stock prefetch
# ---------------------------------------------------------------------------

def prefetch_stocks(stock_client: StockClient, db: BotDB,
                    tickers: list[str], cfg: Config) -> None:
    """Download 60 days of 5m stock history from Yahoo Finance."""
    total = len(tickers)
    logger.info("Stock prefetch: 60d × 5m history for %d tickers...", total)
    for idx, ticker in enumerate(tickers):
        already = db.history_counts([ticker]).get(ticker, 0)
        if already >= 500:  # ~2 days of 5m bars is enough to skip
            logger.debug("[%d/%d] %s already has %d bars, skipping", idx+1, total, ticker, already)
            continue
        try:
            klines = stock_client.fetch_history(ticker, interval=cfg.stock_interval, period="60d")
            if klines:
                db.add_kline_prices(ticker, klines, asset_type="stock")
                logger.info("[%d/%d] %s: %d bars", idx+1, total, ticker, len(klines))
            else:
                logger.warning("[%d/%d] %s: no data", idx+1, total, ticker)
        except Exception as exc:
            logger.error("[%d/%d] %s: prefetch error: %s", idx+1, total, ticker, exc)
        time.sleep(0.5)  # Yahoo Finance rate limit


# ---------------------------------------------------------------------------
# Market building
# ---------------------------------------------------------------------------

def tickers_to_markets(tickers: dict[str, dict]) -> dict[str, Market]:
    """Convert ticker dicts into Market objects (works for both crypto and stocks)."""
    markets: dict[str, Market] = {}
    for symbol, t in tickers.items():
        price = float(t.get("lastPrice", 0) or 0)
        if price <= 0:
            continue
        bid = float(t.get("bidPrice", price) or price) or price
        ask = float(t.get("askPrice", price) or price) or price
        markets[symbol] = Market(
            pair=symbol,
            coin=symbol.removesuffix("USDT") if symbol.endswith("USDT") else symbol,
            price=price,
            bid=bid,
            ask=ask,
            change_24h=float(t.get("priceChangePercent", 0) or 0) / 100.0,
            quote_volume_24h=float(t.get("quoteVolume", 0) or 0),
            amount_precision=6,
            min_order=1.0,
            can_trade=True,
        )
    return markets


# ---------------------------------------------------------------------------
# Prediction storage
# ---------------------------------------------------------------------------

def store_predictions(db: BotDB, result, ts_ms: int, horizon_minutes: int,
                      current_prices: dict[str, float],
                      asset_type: str = "crypto") -> int:
    """Write predictions from a StrategyResult into the DB."""
    if not result.predictor_enabled or not result.predictor_predictions:
        return 0
    target_ms = ts_ms + int(horizon_minutes * 60 * 1000)
    count = 0
    for symbol, pred in result.predictor_predictions.items():
        price = current_prices.get(symbol, 0.0)
        if price <= 0:
            continue
        db.record_prediction(
            timestamp_ms=ts_ms,
            symbol=symbol,
            price=price,
            target_time_ms=target_ms,
            predicted_return=pred.predicted_return,
            probability_up=pred.probability_up,
            probability_flat=pred.probability_flat,
            probability_down=pred.probability_down,
            confidence=pred.confidence,
            model_enabled=True,
            asset_type=asset_type,
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Per-universe poll helpers
# ---------------------------------------------------------------------------

def poll_crypto(client: BinanceClient, db: BotDB, strategy: BossStrategy,
                symbols: list[str], cfg: Config, ts_ms: int) -> tuple[int, int, dict]:
    """Fetch, store, and score one crypto poll cycle. Returns (stored, settled, current_prices)."""
    all_tickers = client.get_all_tickers_map()
    watched = {s: all_tickers[s] for s in symbols if s in all_tickers}
    if not watched:
        logger.warning("Crypto: no tickers received")
        return 0, 0, {}

    db.add_price_snapshots(ts_ms, list(watched.values()), asset_type="crypto")
    markets = tickers_to_markets(watched)
    histories = db.histories(symbols, limit=cfg.predictor_history_limit)

    result = strategy.analyze(histories=histories, markets=markets, now_ms=ts_ms)

    current_prices = {sym: float(t.get("lastPrice", 0)) for sym, t in watched.items()}
    stored = store_predictions(db, result, ts_ms, cfg.prediction_horizon, current_prices, "crypto")
    settled = db.settle_predictions(ts_ms, current_prices, cfg.predictor_class_threshold)

    return stored, settled, current_prices, result


def poll_stocks(stock_client: StockClient, db: BotDB, stock_strategy: BossStrategy,
                tickers: list[str], cfg: Config, ts_ms: int) -> tuple[int, int, dict]:
    """Fetch, store, and score one stock poll cycle. Returns (stored, settled, current_prices, result)."""
    if not is_market_open():
        mins = minutes_until_open()
        logger.info("Stock market closed. Next open in ~%d min.", mins)
        return 0, 0, {}, None

    # Batch fetch current prices
    current_prices = stock_client.fetch_latest_prices(tickers)
    if not current_prices:
        logger.warning("Stocks: no prices received")
        return 0, 0, {}, None

    # Build ticker dicts and store
    snapshots = [
        stock_client.build_market_snapshot(ticker, price)
        for ticker, price in current_prices.items()
    ]
    db.add_price_snapshots(ts_ms, snapshots, asset_type="stock")

    markets = tickers_to_markets({t.get("symbol", ""): t for t in snapshots if t.get("symbol")})
    histories = db.histories(tickers, limit=cfg.predictor_history_limit)

    result = stock_strategy.analyze(histories=histories, markets=markets, now_ms=ts_ms)

    stored = store_predictions(db, result, ts_ms, cfg.prediction_horizon, current_prices, "stock")
    settled = db.settle_predictions(ts_ms, current_prices, cfg.predictor_class_threshold)

    return stored, settled, current_prices, result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(mode: str = "both") -> None:
    cfg = Config()
    cfg.validate()
    setup_logging(cfg.log_file)

    do_crypto = mode in ("crypto", "both")
    do_stocks = mode in ("stock", "both") and cfg.stocks_enabled

    logger.info("=== trade-bot starting === mode=%s", mode)
    logger.info("DB: %s | Crypto: %s | Stocks: %s", cfg.db_path, do_crypto, do_stocks)

    # Clients
    crypto_client = BinanceClient(
        timeout_seconds=cfg.timeout_seconds,
        min_request_interval=cfg.api_min_request_interval_seconds,
    ) if do_crypto else None
    stock_client = StockClient(min_request_interval=0.5) if do_stocks else None

    db = BotDB(cfg.db_path, history_limit=cfg.history_limit)
    trade_logger = TradeLogger(str(Path(cfg.log_file).parent))

    # Separate strategy instances so each universe has its own predictor state
    crypto_strategy = BossStrategy(cfg, shorts_available=False) if do_crypto else None
    stock_strategy  = BossStrategy(cfg, shorts_available=False) if do_stocks else None

    # Determine symbols
    crypto_symbols: list[str] = []
    if do_crypto:
        crypto_symbols = list(cfg.watch_symbols) if cfg.watch_symbols else \
            crypto_client.get_tradable_usdt_symbols(min_quote_volume=cfg.min_quote_volume)[:cfg.diagnostic_top_n]

    stock_tickers: list[str] = list(cfg.watch_stocks) if do_stocks else []

    logger.info("Crypto: %d symbols | Stocks: %d tickers",
                len(crypto_symbols), len(stock_tickers))

    # Prefetch historical data
    if do_crypto:
        prefetch_crypto(crypto_client, db, crypto_symbols, cfg)
    if do_stocks:
        prefetch_stocks(stock_client, db, stock_tickers, cfg)

    last_pred_log_ms = 0
    last_stock_poll_ms = 0
    poll_count = 0

    logger.info("Entering main polling loop (mode=%s). Ctrl+C to stop.", mode)
    while True:
        loop_start = time.monotonic()
        ts_ms = now_ms()

        try:
            stored_c, settled_c, result_c = 0, 0, None
            stored_s, settled_s, result_s = 0, 0, None

            # --- Crypto poll (every poll_seconds) ---
            if do_crypto:
                stored_c, settled_c, prices_c, result_c = poll_crypto(
                    crypto_client, db, crypto_strategy, crypto_symbols, cfg, ts_ms)

            # --- Stock poll (every stock_poll_seconds, only during market hours) ---
            if do_stocks and (ts_ms - last_stock_poll_ms >= cfg.stock_poll_seconds * 1000):
                stored_s, settled_s, prices_s, result_s = poll_stocks(
                    stock_client, db, stock_strategy, stock_tickers, cfg, ts_ms)
                last_stock_poll_ms = ts_ms

            # --- Periodic log ---
            if ts_ms - last_pred_log_ms >= cfg.prediction_log_interval_seconds * 1000:
                last_pred_log_ms = ts_ms

                if result_c:
                    logger.info("CRYPTO | regime=%s model=%s | %s",
                                result_c.regime, result_c.predictor_enabled,
                                result_c.predictor_gate_reason)
                    for sig in result_c.signals[:3]:
                        pred = result_c.predictor_predictions.get(sig.pair)
                        p = f"pred={pred.predicted_return:+.4f} conf={pred.confidence:.2f}" if pred else "no_pred"
                        logger.info("  %-12s score=%.3f %s", sig.pair, sig.score, p)

                if result_s:
                    logger.info("STOCKS | regime=%s model=%s | %s",
                                result_s.regime, result_s.predictor_enabled,
                                result_s.predictor_gate_reason)
                    for sig in result_s.signals[:3]:
                        pred = result_s.predictor_predictions.get(sig.pair)
                        p = f"pred={pred.predicted_return:+.4f} conf={pred.confidence:.2f}" if pred else "no_pred"
                        logger.info("  %-12s score=%.3f %s", sig.pair, sig.score, p)
                elif do_stocks and not is_market_open():
                    logger.info("STOCKS | market closed | next open in ~%d min",
                                minutes_until_open())

            poll_count += 1
            if poll_count % 10 == 0:
                stats = db.stats()
                acc_c = db.prediction_accuracy(asset_type="crypto") if do_crypto else {"n": 0, "directional_accuracy": 0}
                acc_s = db.prediction_accuracy(asset_type="stock")  if do_stocks  else {"n": 0, "directional_accuracy": 0}
                logger.info(
                    "Poll #%d | prices=%d | crypto acc=%.1f%% (n=%d) | stock acc=%.1f%% (n=%d)",
                    poll_count,
                    stats.get("prices_rows", 0),
                    acc_c.get("directional_accuracy", 0) * 100, acc_c.get("n", 0),
                    acc_s.get("directional_accuracy", 0) * 100, acc_s.get("n", 0),
                )

        except KeyboardInterrupt:
            logger.info("Interrupted — shutting down cleanly.")
            break
        except Exception as exc:
            logger.error("Poll error: %s", exc, exc_info=True)
            try:
                db.record_api_event(ts_ms, "poll_loop", False, str(exc))
            except Exception:
                pass

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, cfg.poll_seconds - elapsed))


if __name__ == "__main__":
    run()

"""trade-bot main polling loop.

Fetches public market data from Binance, runs the ML predictor and signal
scoring engine, persists results to SQLite, and logs periodic summaries.
No live trading is performed; this is a prediction and analysis bot only.

Run with:
    python -m src.main
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
# History pre-fetch
# ---------------------------------------------------------------------------

def prefetch_history(client: BinanceClient, db: BotDB, symbols: list[str], cfg: Config,
                     target_bars: int = 3000) -> None:
    """Download historical klines to warm up the predictor.

    Fetches up to `target_bars` candles per symbol by making multiple Binance
    requests (max 1000 per request). 3000 bars of 1m data = ~50 hours, giving
    the ML model a rich training window from the very first poll cycle.

    The predictor gate requires predictor_min_samples cross-sectional rows.
    With 20 symbols × 3000 bars the bot has ~50 000 usable samples on startup,
    so predictions begin immediately after the prefetch completes (~1 minute).
    """
    total = len(symbols)
    logger.info(
        "Pre-fetching up to %d bars per symbol × %d symbols (%s interval)...",
        target_bars, total, cfg.kline_interval,
    )
    for idx, symbol in enumerate(symbols):
        counts = db.history_counts([symbol])
        already = counts.get(symbol, 0)
        if already >= target_bars:
            logger.debug("[%d/%d] %s already has %d bars, skipping", idx + 1, total, symbol, already)
            continue
        try:
            all_klines: list[tuple[int, float]] = []
            end_time_ms: int | None = None
            requests_made = 0

            while len(all_klines) < target_bars:
                remaining = target_bars - len(all_klines)
                batch_limit = min(1000, remaining)
                raw = client.klines(
                    symbol,
                    interval=cfg.kline_interval,
                    limit=batch_limit,
                    end_time_ms=end_time_ms,
                )
                if not raw:
                    break
                batch = [(int(k[0]), float(k[4])) for k in raw if len(k) > 4 and float(k[4]) > 0]
                if not batch:
                    break
                # Prepend older bars in front of what we have
                all_klines = batch + all_klines
                requests_made += 1
                # Next request fetches bars ending just before the oldest we have
                end_time_ms = batch[0][0] - 1
                # Binance paces: stay within weight limits (1200 req/min)
                time.sleep(0.12)
                if len(raw) < batch_limit:
                    break  # Ran out of history

            if all_klines:
                db.add_kline_prices(symbol, all_klines)
                logger.info(
                    "[%d/%d] %s: stored %d bars (%d requests)",
                    idx + 1, total, symbol, len(all_klines), requests_made,
                )
            else:
                logger.warning("[%d/%d] %s: no klines returned", idx + 1, total, symbol)
        except Exception as exc:
            logger.error("[%d/%d] %s: prefetch failed: %s", idx + 1, total, symbol, exc)
        time.sleep(0.12)


# ---------------------------------------------------------------------------
# Market building
# ---------------------------------------------------------------------------

def tickers_to_markets(tickers: dict[str, dict]) -> dict[str, Market]:
    """Convert Binance 24hr ticker dicts into Market objects."""
    markets: dict[str, Market] = {}
    for symbol, t in tickers.items():
        price = float(t.get("lastPrice", 0) or 0)
        if price <= 0:
            continue
        bid = float(t.get("bidPrice", price) or price) or price
        ask = float(t.get("askPrice", price) or price) or price
        markets[symbol] = Market(
            pair=symbol,
            coin=symbol.removesuffix("USDT"),
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

def store_predictions(db: BotDB, result, ts_ms: int, cfg: Config,
                      current_prices: dict[str, float]) -> int:
    """Write fresh predictions from a StrategyResult into the DB."""
    if not result.predictor_enabled or not result.predictor_predictions:
        return 0
    target_ms = ts_ms + int(cfg.prediction_horizon * 60 * 1000)
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
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    cfg = Config()
    cfg.validate()
    setup_logging(cfg.log_file)

    logger.info("=== trade-bot starting ===")
    logger.info("DB: %s | Poll: %ds | Kline interval: %s", cfg.db_path, cfg.poll_seconds, cfg.kline_interval)

    client = BinanceClient(
        timeout_seconds=cfg.timeout_seconds,
        min_request_interval=cfg.api_min_request_interval_seconds,
    )
    db = BotDB(cfg.db_path, history_limit=cfg.history_limit)
    trade_logger = TradeLogger(str(Path(cfg.log_file).parent))
    strategy = BossStrategy(cfg, shorts_available=False)

    # Determine symbols to watch
    if cfg.watch_symbols:
        symbols = list(cfg.watch_symbols)
    else:
        logger.info("No WATCH_SYMBOLS set; discovering liquid USDT pairs from Binance...")
        symbols = client.get_tradable_usdt_symbols(min_quote_volume=cfg.min_quote_volume)
        symbols = symbols[:cfg.diagnostic_top_n]
    logger.info("Watching %d symbols: %s ...", len(symbols), symbols[:5])

    # Warm up historical data before the first poll cycle
    prefetch_history(client, db, symbols, cfg)

    last_prediction_log_ms = 0
    poll_count = 0

    logger.info("Entering main polling loop. Press Ctrl+C to stop.")
    while True:
        loop_start = time.monotonic()
        ts_ms = now_ms()

        try:
            # --- Fetch live tickers ---
            all_tickers = client.get_all_tickers_map()
            watched_tickers = {s: all_tickers[s] for s in symbols if s in all_tickers}

            if not watched_tickers:
                logger.warning("No tickers received from Binance; retrying in 10s")
                time.sleep(10)
                continue

            # --- Store price snapshot ---
            db.add_price_snapshots(ts_ms, list(watched_tickers.values()))

            # --- Build market objects ---
            markets = tickers_to_markets(watched_tickers)

            # --- Load history from DB ---
            histories = db.histories(symbols, limit=cfg.predictor_history_limit)

            # --- Run strategy (signal scoring + ML predictor) ---
            result = strategy.analyze(
                histories=histories,
                markets=markets,
                now_ms=ts_ms,
            )

            # --- Store new predictions ---
            current_prices = {sym: float(t.get("lastPrice", 0)) for sym, t in watched_tickers.items()}
            stored = store_predictions(db, result, ts_ms, cfg, current_prices)

            # --- Settle predictions whose horizon has passed ---
            settled = db.settle_predictions(ts_ms, current_prices, cfg.predictor_class_threshold)

            # --- Periodic summary log ---
            if ts_ms - last_prediction_log_ms >= cfg.prediction_log_interval_seconds * 1000:
                scores = {s.pair: s.score for s in result.signals}
                trade_logger.log_prediction_summary(
                    symbols=symbols,
                    scores=scores,
                    predictions=result.predictor_predictions,
                    regime=result.regime,
                    model_enabled=result.predictor_enabled,
                )
                last_prediction_log_ms = ts_ms

                top = result.signals[:min(5, len(result.signals))]
                logger.info(
                    "--- %s | regime=%s model=%s gate: %s",
                    now_label(), result.regime, result.predictor_enabled,
                    result.predictor_gate_reason,
                )
                for sig in top:
                    pred = result.predictor_predictions.get(sig.pair)
                    pred_str = (
                        f"pred={pred.predicted_return:+.4f} conf={pred.confidence:.2f}"
                        if pred else "no_pred"
                    )
                    logger.info("  %-12s score=%.3f %s", sig.pair, sig.score, pred_str)

            poll_count += 1
            if poll_count % 10 == 0:
                stats = db.stats()
                acc = db.prediction_accuracy()
                logger.info(
                    "Poll #%d | prices=%d preds=%d stored=%d settled=%d | acc=%.1f%% (n=%d)",
                    poll_count,
                    stats.get("prices_rows", 0),
                    stats.get("predictions_rows", 0),
                    stored, settled,
                    acc.get("directional_accuracy", 0.0) * 100,
                    acc.get("n", 0),
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

        # Sleep until next poll
        elapsed = time.monotonic() - loop_start
        sleep_for = max(0.0, cfg.poll_seconds - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()

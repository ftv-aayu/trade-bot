#!/usr/bin/env python3
"""auto_trader.py — Automatic paper trader driven by the bot's ML predictions.

Reads live predictions from the DB, executes paper trades when signal
confidence and predicted return cross thresholds, and holds positions until
the prediction horizon passes or a stop-loss is hit.

Run:
    python auto_trader.py                        # start with default settings
    python auto_trader.py --mode crypto          # crypto only
    python auto_trader.py --mode stock           # stocks only
    python auto_trader.py --trader "Bot-v1"      # custom trader name in arena
    python auto_trader.py --min-conf 0.22        # only act on higher-confidence signals
    python auto_trader.py --dry-run              # show what it would do without trading
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=False)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich.layout import Layout
    from rich.text import Text
    from rich import box
except ImportError:
    print("Install 'rich': pip install rich")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from src.config import Config
from src.db import BotDB
from src.paper_engine import PaperEngine, STARTING_CASH
from src.api.stock_client import is_market_open

IST = ZoneInfo("Asia/Kolkata")
console = Console()

# ---------------------------------------------------------------------------
# Auto-trader parameters (overrideable via CLI)
# ---------------------------------------------------------------------------

DEFAULT_MIN_CONFIDENCE    = 0.20   # minimum prediction confidence to act on
DEFAULT_MIN_PRED_RETURN   = 0.0005 # minimum predicted return to open a long
DEFAULT_POSITION_SIZE_PCT = 0.08   # max 8% of portfolio per position
DEFAULT_STOP_LOSS_PCT     = 0.015  # exit if position falls 1.5%
DEFAULT_TAKE_PROFIT_PCT   = 0.025  # exit if position gains 2.5%
DEFAULT_MAX_POSITIONS     = 8      # hold at most 8 positions at once
DEFAULT_POLL_SECONDS      = 60     # check for signals every 60s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_binance_client = None
_stock_client = None


def _get_binance_client(cfg: Config):
    global _binance_client
    if _binance_client is None:
        from src.api.client import BinanceClient
        _binance_client = BinanceClient(timeout_seconds=cfg.timeout_seconds, min_request_interval=0.0)
    return _binance_client


def _get_stock_client():
    global _stock_client
    if _stock_client is None:
        from src.api.stock_client import StockClient
        _stock_client = StockClient(min_request_interval=0.1)
    return _stock_client


def get_live_prices(db: BotDB, cfg: Config) -> dict[str, float]:
    """Fetch prices directly from Binance + Yahoo Finance, DB as fallback.
    Makes the auto-trader independent of the main bot process."""
    prices: dict[str, float] = {}
    try:
        tickers = _get_binance_client(cfg).get_all_tickers_map()
        for sym in cfg.watch_symbols:
            t = tickers.get(sym)
            if t:
                p = float(t.get("lastPrice", 0) or 0)
                if p > 0:
                    prices[sym] = p
    except Exception:
        pass
    if cfg.stocks_enabled and cfg.watch_stocks:
        try:
            stock_prices = _get_stock_client().fetch_latest_prices(list(cfg.watch_stocks))
            prices.update(stock_prices)
        except Exception:
            pass
    missing = [s for s in list(cfg.watch_symbols) + list(cfg.watch_stocks) if s not in prices]
    if missing:
        prices.update(db.latest_prices(missing))
    return prices


def get_latest_predictions(db: BotDB, asset_type: str | None = None) -> dict[str, dict]:
    """Return the most recent prediction per symbol as {symbol: row_dict}."""
    rows = db.recent_predictions(limit=200, asset_type=asset_type)
    latest: dict[str, dict] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in latest:
            latest[sym] = r
    return latest


# ---------------------------------------------------------------------------
# Signal evaluation
# ---------------------------------------------------------------------------

def evaluate_signals(
    predictions: dict[str, dict],
    current_prices: dict[str, float],
    current_positions: set[str],
    min_confidence: float,
    min_pred_return: float,
    max_positions: int,
    asset_type: str | None,
) -> list[dict]:
    """Return list of actionable BUY signals sorted by predicted return."""
    signals = []
    for sym, pred in predictions.items():
        if sym in current_positions:
            continue
        if len(current_positions) + len(signals) >= max_positions:
            break
        if sym not in current_prices or current_prices[sym] <= 0:
            continue

        pred_ret  = float(pred.get("predicted_return", 0))
        conf      = float(pred.get("confidence", 0))
        model_on  = bool(pred.get("model_enabled", False))

        if not model_on:
            continue
        if conf < min_confidence:
            continue
        if pred_ret < min_pred_return:
            continue
        # Stocks: skip if market closed
        if asset_type == "stock" and not is_market_open():
            continue

        signals.append({
            "symbol":           sym,
            "predicted_return": pred_ret,
            "confidence":       conf,
            "price":            current_prices[sym],
        })

    signals.sort(key=lambda x: -x["predicted_return"])
    return signals


# ---------------------------------------------------------------------------
# Rich dashboard
# ---------------------------------------------------------------------------

def make_dashboard(
    engine: PaperEngine,
    trader_id: str,
    prices: dict[str, float],
    predictions: dict[str, dict],
    log_lines: list[str],
    args: argparse.Namespace,
) -> Layout:
    snap = engine.snapshot(trader_id, prices)
    now  = datetime.now(IST).strftime("%H:%M:%S IST")
    pnl_color = "green" if snap.pnl_pct >= 0 else "red"

    # Header
    header_text = Text()
    header_text.append(" 🤖 Auto Trader ", style="bold white on dark_blue")
    header_text.append(f" {trader_id} ", style="cyan")
    header_text.append(f" ${snap.total_value:,.2f} ", style=f"bold {pnl_color}")
    header_text.append(f"({snap.pnl_pct:+.2f}%) ", style=pnl_color)
    header_text.append(f" {now} ", style="dim")
    if args.dry_run:
        header_text.append(" [DRY RUN] ", style="bold yellow on dark_red")

    # Positions table
    pos_tbl = Table(title="Open Positions", box=box.SIMPLE_HEAVY, header_style="bold cyan", expand=True)
    pos_tbl.add_column("Symbol",   style="bold white", width=12)
    pos_tbl.add_column("Qty",      justify="right",    width=10)
    pos_tbl.add_column("Entry",    justify="right",    width=10)
    pos_tbl.add_column("Now",      justify="right",    width=10)
    pos_tbl.add_column("P&L",      justify="right",    width=12)
    pos_tbl.add_column("P&L %",    justify="right",    width=8)
    pos_tbl.add_column("Stop",     justify="right",    width=10)
    pos_tbl.add_column("Target",   justify="right",    width=10)

    for p in sorted(snap.positions, key=lambda x: -abs(x.unrealized_pnl)):
        clr = "green" if p.unrealized_pnl >= 0 else "red"
        stop_px   = p.avg_cost * (1 - args.stop_loss)
        target_px = p.avg_cost * (1 + args.take_profit)
        pos_tbl.add_row(
            p.symbol,
            f"{p.qty:.4f}",
            f"${p.avg_cost:.4f}",
            f"${p.current_price:.4f}",
            Text(f"${p.unrealized_pnl:+,.2f}", style=clr),
            Text(f"{p.unrealized_pnl_pct:+.2f}%", style=clr),
            f"${stop_px:.4f}",
            f"${target_px:.4f}",
        )
    if not snap.positions:
        pos_tbl.add_row("[dim]No positions yet[/]", "", "", "", "", "", "", "")

    # Top predictions
    pred_tbl = Table(title="Top Signals", box=box.SIMPLE, header_style="bold green", expand=True)
    pred_tbl.add_column("Symbol",  style="bold white", width=12)
    pred_tbl.add_column("Pred",    justify="right",    width=10)
    pred_tbl.add_column("Conf",    justify="right",    width=7)
    pred_tbl.add_column("Status",  justify="center",   width=10)

    top_preds = sorted(predictions.items(), key=lambda x: -float(x[1].get("predicted_return", 0)))[:10]
    held = {p.symbol for p in snap.positions}
    for sym, pred in top_preds:
        pred_ret = float(pred.get("predicted_return", 0))
        conf     = float(pred.get("confidence", 0))
        clr      = "green" if pred_ret > 0 else "red"
        if sym in held:
            status = Text("HELD", style="cyan")
        elif conf >= args.min_conf and pred_ret >= args.min_return:
            status = Text("SIGNAL ▲", style="bold green")
        else:
            status = Text("wait", style="dim")
        pred_tbl.add_row(sym, Text(f"{pred_ret:+.4f}", style=clr), f"{conf:.2f}", status)

    # Activity log
    log_text = Text()
    log_text.append("Activity Log\n", style="bold dim")
    for line in log_lines[-12:]:
        log_text.append(line + "\n", style="dim")

    # Layout assembly
    layout = Layout()
    layout.split_column(
        Layout(Panel(header_text, style="blue"), size=3),
        Layout(pos_tbl, name="positions"),
        Layout(name="bottom"),
    )
    layout["bottom"].split_row(
        Layout(pred_tbl, ratio=1),
        Layout(Panel(log_text, border_style="dim"), ratio=1),
    )
    return layout


# ---------------------------------------------------------------------------
# Main trading loop
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    cfg    = Config()
    db     = BotDB(cfg.db_path, history_limit=cfg.history_limit)
    engine = PaperEngine(cfg.db_path)

    trader_id = args.trader
    is_new = engine.register(trader_id)
    if is_new:
        console.print(f"[green]New auto-trader wallet created: {trader_id} (${STARTING_CASH:,.0f})[/]")
    else:
        prices = get_live_prices(db, cfg)
        snap   = engine.snapshot(trader_id, prices)
        console.print(f"[cyan]Resuming {trader_id} — portfolio: ${snap.total_value:,.2f} ({snap.pnl_pct:+.2f}%)[/]")

    asset_type = None if args.mode == "both" else args.mode
    log_lines: list[str] = []

    def log(msg: str) -> None:
        ts = datetime.now(IST).strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_lines.append(line)
        if len(log_lines) > 100:
            log_lines.pop(0)

    log(f"Started in {args.mode} mode | min_conf={args.min_conf:.2f} min_ret={args.min_return:.4f}")

    try:
        with Live(screen=True, refresh_per_second=1) as live:
            while True:
                loop_start = time.monotonic()

                prices      = get_live_prices(db, cfg)
                predictions = get_latest_predictions(db, asset_type)
                snap        = engine.snapshot(trader_id, prices)
                held_syms   = {p.symbol for p in snap.positions}

                # --- EXIT: check stop-loss and take-profit on open positions ---
                for pos in snap.positions:
                    pnl_pct = pos.unrealized_pnl_pct / 100
                    reason  = None
                    if pnl_pct <= -args.stop_loss:
                        reason = f"STOP-LOSS ({pnl_pct:+.2%})"
                    elif pnl_pct >= args.take_profit:
                        reason = f"TAKE-PROFIT ({pnl_pct:+.2%})"

                    if reason:
                        if not args.dry_run:
                            result = engine.sell(trader_id, pos.symbol, 1.0,
                                                 pos.current_price, is_pct=True,
                                                 note=f"auto:{reason}")
                            if result["ok"]:
                                log(f"SELL {pos.symbol} {reason} P&L=${result['realized_pnl']:+,.2f}")
                        else:
                            log(f"[DRY] SELL {pos.symbol} {reason}")
                        held_syms.discard(pos.symbol)

                # Refresh snapshot after exits
                snap = engine.snapshot(trader_id, prices)
                held_syms = {p.symbol for p in snap.positions}

                # --- ENTRY: find new signals ---
                signals = evaluate_signals(
                    predictions, prices, held_syms,
                    args.min_conf, args.min_return,
                    args.max_positions, asset_type,
                )

                for sig in signals:
                    if len(held_syms) >= args.max_positions:
                        break
                    position_usd = snap.cash * args.position_size
                    position_usd = min(position_usd, snap.cash * 0.95)  # never risk more than 95% of cash
                    if position_usd < 10:
                        log("Low cash, skipping entry")
                        break

                    if not args.dry_run:
                        result = engine.buy(trader_id, sig["symbol"], position_usd,
                                            sig["price"],
                                            note=f"auto:pred={sig['predicted_return']:+.4f} conf={sig['confidence']:.2f}")
                        if result["ok"]:
                            log(f"BUY  {sig['symbol']} ${position_usd:,.0f} @ ${sig['price']:.4f}  pred={sig['predicted_return']:+.4f}")
                            held_syms.add(sig["symbol"])
                    else:
                        log(f"[DRY] BUY {sig['symbol']} ${position_usd:,.0f} pred={sig['predicted_return']:+.4f}")

                # Refresh for display
                prices      = get_live_prices(db, cfg)
                predictions = get_latest_predictions(db, asset_type)
                live.update(make_dashboard(engine, trader_id, prices, predictions, log_lines, args))

                elapsed = time.monotonic() - loop_start
                time.sleep(max(0.0, args.poll - elapsed))

    except KeyboardInterrupt:
        prices = get_live_prices(db, cfg)
        snap   = engine.snapshot(trader_id, prices)
        score  = engine.score_session(trader_id, prices)
        console.print(Panel(
            f"[bold]Final Portfolio:[/] ${snap.total_value:,.2f}  ({snap.pnl_pct:+.2f}%)\n"
            f"[bold]Session Score:[/]   {score['score']:.3f} / 1.000\n"
            f"Positions closed: {len(snap.positions)} still open\n"
            f"P&L component:    {score['pnl_component']:.3f}\n"
            f"Diversity:        {score['diversification_component']:.3f}\n"
            f"Discipline:       {score['discipline_component']:.3f}",
            title="🤖 Auto Trader — Session Complete", border_style="yellow"
        ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="auto_trader", description="Automatic paper trader")
    parser.add_argument("--trader",       default="AutoBot",  help="Trader name in the arena (default: AutoBot)")
    parser.add_argument("--mode",         choices=["crypto","stock","both"], default="both")
    parser.add_argument("--min-conf",     type=float, default=DEFAULT_MIN_CONFIDENCE,   dest="min_conf",      help=f"Min prediction confidence (default {DEFAULT_MIN_CONFIDENCE})")
    parser.add_argument("--min-return",   type=float, default=DEFAULT_MIN_PRED_RETURN,  dest="min_return",    help=f"Min predicted return to open long (default {DEFAULT_MIN_PRED_RETURN})")
    parser.add_argument("--position-size",type=float, default=DEFAULT_POSITION_SIZE_PCT,dest="position_size", help=f"Fraction of portfolio per trade (default {DEFAULT_POSITION_SIZE_PCT})")
    parser.add_argument("--stop-loss",    type=float, default=DEFAULT_STOP_LOSS_PCT,    dest="stop_loss",     help=f"Stop-loss fraction, e.g. 0.015 = 1.5pct (default {DEFAULT_STOP_LOSS_PCT})")
    parser.add_argument("--take-profit",  type=float, default=DEFAULT_TAKE_PROFIT_PCT,  dest="take_profit",   help=f"Take-profit fraction, e.g. 0.025 = 2.5pct (default {DEFAULT_TAKE_PROFIT_PCT})")
    parser.add_argument("--max-positions",type=int,   default=DEFAULT_MAX_POSITIONS,    dest="max_positions", help=f"Max open positions (default {DEFAULT_MAX_POSITIONS})")
    parser.add_argument("--poll",         type=int,   default=DEFAULT_POLL_SECONDS,     help=f"Poll interval in seconds (default {DEFAULT_POLL_SECONDS})")
    parser.add_argument("--dry-run",      action="store_true", dest="dry_run",          help="Print signals without placing orders")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()

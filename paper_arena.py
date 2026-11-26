#!/usr/bin/env python3
"""paper_arena.py — Manual paper trading terminal for the cross-dev team demo.

Each participant gets a virtual $100,000 wallet. They can BUY and SELL any
watched symbol (crypto or stock) at live prices fetched DIRECTLY from
Binance and Yahoo Finance — independent of whether the main bot is running.
The DB is used only as a fallback if the live fetch fails.

Run:
    python paper_arena.py                    # enter trader name interactively
    python paper_arena.py --trader Alice     # skip the name prompt
    python paper_arena.py --leaderboard      # show only the leaderboard
    python paper_arena.py --reset Alice      # reset a trader back to $100k
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
    from rich.prompt import Prompt, Confirm
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

IST = ZoneInfo("Asia/Kolkata")
console = Console()


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------
# Price fetching — live API first, DB fallback
# ---------------------------------------------------------------------------

# Module-level client instances (created once, reused on every refresh)
_binance_client = None
_stock_client = None


def _get_binance_client(cfg: Config):
    global _binance_client
    if _binance_client is None:
        from src.api.client import BinanceClient
        _binance_client = BinanceClient(
            timeout_seconds=cfg.timeout_seconds,
            min_request_interval=0.0,
        )
    return _binance_client


def _get_stock_client():
    global _stock_client
    if _stock_client is None:
        from src.api.stock_client import StockClient
        _stock_client = StockClient(min_request_interval=0.1)
    return _stock_client


def get_live_prices(db: BotDB, cfg: Config) -> dict[str, float]:
    """Fetch prices directly from Binance + Yahoo Finance.
    Falls back to the DB only if the live fetch fails.
    This makes the arena fully independent of the main bot process.
    """
    prices: dict[str, float] = {}

    # --- Crypto: single request gets all USDT tickers ---
    try:
        client = _get_binance_client(cfg)
        tickers = client.get_all_tickers_map()
        for sym in cfg.watch_symbols:
            t = tickers.get(sym)
            if t:
                p = float(t.get("lastPrice", 0) or 0)
                if p > 0:
                    prices[sym] = p
    except Exception:
        pass  # fall through to DB

    # --- Stocks: batch fetch via yfinance ---
    if cfg.stocks_enabled and cfg.watch_stocks:
        try:
            sc = _get_stock_client()
            stock_prices = sc.fetch_latest_prices(list(cfg.watch_stocks))
            prices.update(stock_prices)
        except Exception:
            pass

    # --- DB fallback for any symbol we failed to get ---
    missing = [s for s in list(cfg.watch_symbols) + list(cfg.watch_stocks) if s not in prices]
    if missing:
        db_prices = db.latest_prices(missing)
        prices.update(db_prices)

    return prices


def all_symbols(cfg: Config) -> list[str]:
    return sorted(list(cfg.watch_symbols) + list(cfg.watch_stocks))


# ---------------------------------------------------------------------------
# Rich rendering helpers
# ---------------------------------------------------------------------------

def portfolio_panel(engine: PaperEngine, trader_id: str, prices: dict[str, float]) -> Panel:
    snap = engine.snapshot(trader_id, prices)
    pnl_color = "green" if snap.pnl_pct >= 0 else "red"
    t = Text()
    t.append(f"  Trader: ", style="dim"); t.append(f"{trader_id}\n", style="bold white")
    t.append(f"  Cash:   ", style="dim"); t.append(f"${snap.cash:>12,.2f}\n", style="white")
    t.append(f"  Assets: ", style="dim"); t.append(f"${snap.positions_value:>12,.2f}\n", style="white")
    t.append(f"  Total:  ", style="dim"); t.append(f"${snap.total_value:>12,.2f}\n", style="bold white")
    t.append(f"  P&L:    ", style="dim")
    t.append(f"${snap.unrealized_pnl:>+12,.2f}  ({snap.pnl_pct:+.2f}%)\n", style=f"bold {pnl_color}")
    return Panel(t, title="💼 My Portfolio", border_style=pnl_color)


def positions_table(engine: PaperEngine, trader_id: str, prices: dict[str, float]) -> Table:
    snap = engine.snapshot(trader_id, prices)
    tbl = Table(title="Open Positions", box=box.SIMPLE_HEAVY, header_style="bold cyan")
    tbl.add_column("Symbol",    style="bold white", width=12)
    tbl.add_column("Qty",       justify="right",    width=12)
    tbl.add_column("Avg Cost",  justify="right",    width=10)
    tbl.add_column("Price Now", justify="right",    width=10)
    tbl.add_column("Value",     justify="right",    width=12)
    tbl.add_column("Unr. P&L",  justify="right",    width=14)
    tbl.add_column("P&L %",     justify="right",    width=8)

    if not snap.positions:
        tbl.add_row("[dim]No open positions[/]", "", "", "", "", "", "")
    for p in sorted(snap.positions, key=lambda x: -abs(x.unrealized_pnl)):
        clr = "green" if p.unrealized_pnl >= 0 else "red"
        tbl.add_row(
            p.symbol,
            f"{p.qty:.4f}",
            f"${p.avg_cost:.4f}",
            f"${p.current_price:.4f}",
            f"${p.value:,.2f}",
            Text(f"${p.unrealized_pnl:+,.2f}", style=clr),
            Text(f"{p.unrealized_pnl_pct:+.2f}%", style=clr),
        )
    return tbl


def orders_table(engine: PaperEngine, trader_id: str) -> Table:
    orders = engine.order_history(trader_id, limit=10)
    tbl = Table(title="Recent Orders (last 10)", box=box.SIMPLE, header_style="bold dim")
    tbl.add_column("Time",   style="dim",        width=10)
    tbl.add_column("Symbol", style="bold white", width=12)
    tbl.add_column("Side",   justify="center",   width=6)
    tbl.add_column("Qty",    justify="right",    width=12)
    tbl.add_column("Price",  justify="right",    width=10)
    tbl.add_column("Total",  justify="right",    width=12)

    for o in orders:
        ts = datetime.fromtimestamp(o["timestamp_ms"]/1000, tz=IST).strftime("%H:%M:%S")
        side_text = Text("BUY",  style="green") if o["side"] == "BUY" else Text("SELL", style="red")
        tbl.add_row(ts, o["symbol"], side_text,
                    f"{float(o['qty']):.4f}", f"${float(o['price']):.4f}",
                    f"${float(o['notional']):,.2f}")
    if not orders:
        tbl.add_row("[dim]No orders yet[/]", "", "", "", "", "")
    return tbl


def leaderboard_table(engine: PaperEngine, prices: dict[str, float]) -> Table:
    board = engine.leaderboard(prices)
    tbl = Table(title="🏆 Leaderboard", box=box.SIMPLE_HEAVY, header_style="bold yellow")
    tbl.add_column("#",        justify="right", width=4)
    tbl.add_column("Trader",   style="bold white", width=16)
    tbl.add_column("Value",    justify="right",    width=14)
    tbl.add_column("P&L %",    justify="right",    width=10)
    tbl.add_column("Cash",     justify="right",    width=12)
    tbl.add_column("Positions",justify="center",   width=10)

    for i, row in enumerate(board, 1):
        clr = "green" if row["pnl_pct"] >= 0 else "red"
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f" {i}")
        tbl.add_row(
            str(medal),
            row["trader_id"],
            f"${row['total_value']:>12,.2f}",
            Text(f"{row['pnl_pct']:+.2f}%", style=clr),
            f"${row['cash']:>10,.2f}",
            str(row["positions"]),
        )
    if not board:
        tbl.add_row("", "[dim]No traders yet[/]", "", "", "", "")
    return tbl


def predictions_panel(db: BotDB) -> Panel:
    """Show latest ML predictions as buy/sell hints."""
    rows_c = db.recent_predictions(limit=40, asset_type="crypto")
    rows_s = db.recent_predictions(limit=40, asset_type="stock")

    seen: set[str] = set()
    top: list[tuple[str, float, float]] = []
    for r in rows_c + rows_s:
        s = r["symbol"]
        if s in seen: continue
        seen.add(s)
        top.append((s, float(r["predicted_return"]), float(r["confidence"])))

    top_buys  = sorted(top, key=lambda x: -x[1])[:5]
    top_sells = sorted(top, key=lambda x: x[1])[:3]

    t = Text()
    t.append("Top BUY signals:\n", style="bold green")
    for sym, pred, conf in top_buys:
        t.append(f"  {sym:<12} pred={pred:+.4f}  conf={conf:.2f}\n", style="green")
    t.append("\nTop SELL signals:\n", style="bold red")
    for sym, pred, conf in top_sells:
        t.append(f"  {sym:<12} pred={pred:+.4f}  conf={conf:.2f}\n", style="red")
    t.append("\n[dim]Advisory only — your call[/]")
    return Panel(t, title="🤖 Bot Predictions", border_style="dim")


# ---------------------------------------------------------------------------
# Interactive trading session
# ---------------------------------------------------------------------------

def trading_session(trader_id: str, engine: PaperEngine,
                    db: BotDB, cfg: Config) -> None:
    prices = get_live_prices(db, cfg)
    console.clear()

    while True:
        prices = get_live_prices(db, cfg)

        console.print(portfolio_panel(engine, trader_id, prices))
        console.print(positions_table(engine, trader_id, prices))
        console.print(orders_table(engine, trader_id))
        console.print(predictions_panel(db))
        console.print(leaderboard_table(engine, prices))

        console.print("\n[bold]Commands:[/]  [green]buy[/]  [red]sell[/]  [cyan]refresh[/]  [yellow]score[/]  [dim]quit[/]")
        cmd = Prompt.ask("> ").strip().lower()

        if cmd in ("q", "quit", "exit"):
            score = engine.score_session(trader_id, prices)
            console.print(Panel(
                f"[bold]Session Score: {score['score']:.3f} / 1.000[/]\n\n"
                f"P&L:             ${score['total_value']:,.2f}  ({score['pnl_pct']:+.2f}%)\n"
                f"P&L component:   {score['pnl_component']:.3f}\n"
                f"Diversity:       {score['diversification_component']:.3f} ({score['unique_symbols_traded']} symbols)\n"
                f"Discipline:      {score['discipline_component']:.3f}\n"
                f"Total orders:    {score['total_orders']}",
                title="📊 Final Score", border_style="yellow"
            ))
            break

        elif cmd in ("b", "buy"):
            sym = Prompt.ask("Symbol (e.g. BTCUSDT or AAPL)").strip().upper()
            if sym not in prices:
                console.print(f"[red]Unknown symbol {sym}. Available: {', '.join(list(prices.keys())[:10])}...[/]")
                continue
            price = prices[sym]
            snap = engine.snapshot(trader_id, prices)
            console.print(f"[dim]{sym} current price: ${price:,.4f} | Your cash: ${snap.cash:,.2f}[/]")
            amt_str = Prompt.ask("Amount in USD to spend")
            try:
                amt = float(amt_str)
            except ValueError:
                console.print("[red]Invalid amount[/]"); continue
            result = engine.buy(trader_id, sym, amt, price)
            if result["ok"]:
                console.print(f"[green]✓ Bought {result['qty']:.4f} {sym} @ ${price:,.4f}  fee=${result['fee']:.2f}[/]")
            else:
                console.print(f"[red]✗ {result['error']}[/]")

        elif cmd in ("s", "sell"):
            snap = engine.snapshot(trader_id, prices)
            if not snap.positions:
                console.print("[yellow]No open positions to sell.[/]"); continue
            sym = Prompt.ask("Symbol to sell").strip().upper()
            pos = next((p for p in snap.positions if p.symbol == sym), None)
            if not pos:
                console.print(f"[red]No position in {sym}[/]"); continue
            console.print(f"[dim]You hold {pos.qty:.4f} {sym} @ ${prices[sym]:,.4f}  P&L: ${pos.unrealized_pnl:+,.2f}[/]")
            pct_str = Prompt.ask("Sell what % of holding? (1–100)", default="100")
            try:
                pct = float(pct_str) / 100
            except ValueError:
                console.print("[red]Invalid %[/]"); continue
            result = engine.sell(trader_id, sym, pct, prices[sym], is_pct=True)
            if result["ok"]:
                clr = "green" if result["realized_pnl"] >= 0 else "red"
                console.print(f"[{clr}]✓ Sold {result['qty']:.4f} {sym} @ ${prices[sym]:,.4f}  P&L=${result['realized_pnl']:+,.2f}[/{clr}]")
            else:
                console.print(f"[red]✗ {result['error']}[/]")

        elif cmd in ("score",):
            score = engine.score_session(trader_id, prices)
            console.print(Panel(
                f"Score: [bold yellow]{score['score']:.3f}[/] / 1.000\n"
                f"P&L: {score['pnl_pct']:+.2f}%  |  Diversity: {score['unique_symbols_traded']} symbols  |  Orders: {score['total_orders']}",
                title="Current Score", border_style="yellow"
            ))

        elif cmd in ("r", "refresh"):
            pass  # loop continues and redraws

        else:
            console.print("[dim]Unknown command. Use: buy, sell, refresh, score, quit[/]")

        time.sleep(0.3)
        console.clear()


# ---------------------------------------------------------------------------
# Leaderboard-only live view
# ---------------------------------------------------------------------------

def live_leaderboard(engine: PaperEngine, db: BotDB, cfg: Config) -> None:
    console.print("[bold cyan]Live Leaderboard[/] — Ctrl+C to exit")
    try:
        with Live(screen=True, refresh_per_second=1) as live:
            while True:
                prices = get_live_prices(db, cfg)
                now = datetime.now(IST).strftime("%H:%M:%S IST")
                layout = Layout()
                layout.split_column(
                    Layout(Panel(Text(f" trade-bot  Paper Arena  {now} ", style="bold white on dark_blue"), style="blue"), size=3),
                    Layout(leaderboard_table(engine, prices)),
                )
                live.update(layout)
                time.sleep(5)
    except KeyboardInterrupt:
        console.print("\n[dim]Leaderboard closed.[/]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="paper_arena", description="Paper Trading Arena")
    parser.add_argument("--trader",      metavar="NAME",  help="Trader name (skips prompt)")
    parser.add_argument("--leaderboard", action="store_true", help="Show live leaderboard only")
    parser.add_argument("--reset",       metavar="NAME",  help="Reset a trader to $100k")
    parser.add_argument("--score",       metavar="NAME",  help="Print final score for a trader and exit")
    args = parser.parse_args()

    cfg = Config()
    db  = BotDB(cfg.db_path, history_limit=cfg.history_limit)
    engine = PaperEngine(cfg.db_path)

    if args.reset:
        engine.reset(args.reset)
        console.print(f"[green]✓ {args.reset} reset to ${STARTING_CASH:,.0f}[/]")
        return

    if args.score:
        prices = get_live_prices(db, cfg)
        score = engine.score_session(args.score, prices)
        console.print_json(__import__("json").dumps(score, indent=2))
        return

    if args.leaderboard:
        live_leaderboard(engine, db, cfg)
        return

    # Interactive trading session
    if args.trader:
        trader_id = args.trader.strip()
    else:
        console.print(Panel(
            "[bold cyan]Welcome to the Paper Trading Arena![/]\n\n"
            "You start with [bold]$100,000[/] in virtual cash.\n"
            "Trade crypto and stocks at live prices.\n"
            "Compete on the leaderboard!",
            title="📈 trade-bot Paper Arena", border_style="cyan"
        ))
        trader_id = Prompt.ask("Enter your name").strip()

    if not trader_id:
        console.print("[red]No name entered.[/]"); return

    is_new = engine.register(trader_id)
    if is_new:
        console.print(f"[green]Welcome, {trader_id}! You have ${STARTING_CASH:,.0f} to trade.[/]")
    else:
        prices = get_live_prices(db, cfg)
        snap = engine.snapshot(trader_id, prices)
        console.print(f"[cyan]Welcome back, {trader_id}! Portfolio value: ${snap.total_value:,.2f} ({snap.pnl_pct:+.2f}%)[/]")

    trading_session(trader_id, engine, db, cfg)


if __name__ == "__main__":
    main()

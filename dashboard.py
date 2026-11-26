#!/usr/bin/env python3
"""Terminal dashboard for trade-bot. Reads live data from the SQLite DB.

Shows crypto and stock predictions in separate panels.
Refreshes every DASHBOARD_REFRESH seconds (default: 5).

Run:
    python dashboard.py
"""
from __future__ import annotations

import os
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
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich.columns import Columns
    from rich import box
except ImportError:
    print("Install 'rich': pip install rich")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from src.config import Config
from src.db import BotDB
from src.api.stock_client import is_market_open, minutes_until_open

IST = ZoneInfo("Asia/Kolkata")
console = Console()


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=IST).strftime("%H:%M:%S") if ms else "-"


def make_header(stats: dict) -> Panel:
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    prices = stats.get("prices_rows", 0)
    preds  = stats.get("predictions_rows", 0)
    mkt    = "[green]OPEN[/]" if is_market_open() else f"[dim]CLOSED (opens in ~{minutes_until_open()}m)[/]"
    t = Text()
    t.append(" ● trade-bot ", style="bold white on dark_blue")
    t.append(f" {now} ", style="cyan")
    t.append(f" {prices:,} prices · {preds:,} predictions ", style="dim")
    t.append(f" US Market: {mkt} ")
    return Panel(t, style="blue", height=3)


def predictions_table(db: BotDB, asset_type: str, title: str) -> Table:
    import time as _time
    rows = db.recent_predictions(limit=60, asset_type=asset_type)
    color = "cyan" if asset_type == "crypto" else "yellow"

    # Work out how stale the newest prediction is
    stale_note = ""
    if rows:
        newest_ms = max(int(r.get("timestamp_ms", 0)) for r in rows)
        age_min = (_time.time()*1000 - newest_ms) / 60000
        if age_min > 60:
            stale_note = f"  [red](last updated {age_min:.0f}m ago — model gated)[/]"
        elif age_min > 10:
            stale_note = f"  [yellow](last updated {age_min:.0f}m ago)[/]"
    if asset_type == "stock" and not is_market_open():
        stale_note = f"  [dim](market closed · opens in ~{minutes_until_open()}m · showing last session)[/]"

    table = Table(
        title=title + stale_note,
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {color}",
        expand=True,
        show_lines=False,
    )
    table.add_column("Symbol",     style="bold white", width=12)
    table.add_column("Direction",  justify="center",   width=6)
    table.add_column("Pred Ret",   justify="right",    width=10)
    table.add_column("Conf",       justify="right",    width=7)
    table.add_column("P(Up)",      justify="right",    width=7, style="green")
    table.add_column("P(Dn)",      justify="right",    width=7, style="red")
    table.add_column("Age",        style="dim",        width=7)

    seen: set[str] = set()
    now_ms = int(_time.time() * 1000)
    for r in rows:
        sym = str(r.get("symbol", ""))
        if sym in seen:
            continue
        seen.add(sym)
        pred  = float(r.get("predicted_return", 0))
        conf  = float(r.get("confidence", 0))
        p_up  = float(r.get("probability_up", 0))
        p_dn  = float(r.get("probability_down", 0))
        ts    = int(r.get("timestamp_ms", 0))
        age_m = (now_ms - ts) / 60000 if ts else 0

        # Colour the age: fresh=dim, >30m=yellow, >2h=red
        if age_m > 120:
            age_str = Text(f"{age_m:.0f}m", style="red")
        elif age_m > 30:
            age_str = Text(f"{age_m:.0f}m", style="yellow")
        else:
            age_str = Text(f"{age_m:.0f}m", style="dim")

        direction = Text("▲ UP", style="green") if pred > 0 else Text("▼ DN", style="red")
        pred_text = Text(f"{pred:+.4%}", style="green" if pred > 0 else "red")
        table.add_row(sym, direction, pred_text,
                      f"{conf:.0%}", f"{p_up:.0%}", f"{p_dn:.0%}", age_str)

    if not seen:
        if asset_type == "stock" and not is_market_open():
            table.add_row(f"[dim]Market closed. Opens in ~{minutes_until_open()} min.[/]",
                          "", "", "", "", "", "")
        else:
            table.add_row("[dim]No predictions yet[/]", "", "", "", "", "", "")
    return table


def accuracy_panel(db: BotDB, asset_type: str, label: str) -> Panel:
    acc = db.prediction_accuracy(asset_type=asset_type)
    n   = acc.get("n", 0)
    da  = acc.get("directional_accuracy", 0.0)
    t = Text()
    if n == 0:
        t.append("Waiting for settled predictions…\n", style="dim")
        t.append("(predictions settle after the 60-min horizon passes)", style="dim")
    else:
        t.append("Settled:  ", style="dim"); t.append(f"{n:,}\n", style="bold white")
        t.append("Dir acc:  ", style="dim")
        clr = "green" if da > 0.55 else "yellow" if da > 0.50 else "red"
        t.append(f"{da:.1%}\n", style=f"bold {clr}")
    return Panel(t, title=label, border_style="green" if da > 0.50 or n == 0 else "red")


def stats_panel(db: BotDB, cfg: Config) -> Panel:
    counts_c = db.history_counts(list(cfg.watch_symbols[:8]))
    counts_s = db.history_counts(list(cfg.watch_stocks[:8])) if cfg.stocks_enabled else {}
    t = Text()
    t.append("Crypto bars (top 5):\n", style="bold cyan")
    for sym, cnt in sorted(counts_c.items(), key=lambda x: -x[1])[:5]:
        bar = "█" * min(16, cnt // 200)
        t.append(f"  {sym:<12} {cnt:>5}  {bar}\n", style="white")
    if counts_s:
        t.append("\nStock bars (top 5):\n", style="bold yellow")
        for sym, cnt in sorted(counts_s.items(), key=lambda x: -x[1])[:5]:
            bar = "█" * min(16, cnt // 100)
            t.append(f"  {sym:<10} {cnt:>5}  {bar}\n", style="white")
    return Panel(t, title="History Depth", border_style="blue")


def render(db: BotDB, cfg: Config, do_crypto: bool = True, do_stocks: bool = True) -> Layout:
    stats = db.stats()

    if do_crypto and do_stocks:
        layout = Layout()
        layout.split_column(
            Layout(make_header(stats), name="header", size=3),
            Layout(name="main"),
            Layout(name="bottom", size=10),
        )
        layout["main"].split_row(
            Layout(predictions_table(db, "crypto", "🪙 Crypto Predictions"), ratio=1),
            Layout(predictions_table(db, "stock",  "📈 Stock Predictions"),  ratio=1),
        )
        layout["bottom"].split_row(
            Layout(accuracy_panel(db, "crypto", "Crypto Accuracy"), ratio=1),
            Layout(accuracy_panel(db, "stock",  "Stock Accuracy"),  ratio=1),
            Layout(stats_panel(db, cfg),                            ratio=1),
        )
    elif do_crypto:
        layout = Layout()
        layout.split_column(
            Layout(make_header(stats), name="header", size=3),
            Layout(predictions_table(db, "crypto", "🪙 Crypto Predictions"), name="main"),
            Layout(name="bottom", size=10),
        )
        layout["bottom"].split_row(
            Layout(accuracy_panel(db, "crypto", "Crypto Accuracy"), ratio=2),
            Layout(stats_panel(db, cfg),                            ratio=1),
        )
    else:  # stocks only
        layout = Layout()
        layout.split_column(
            Layout(make_header(stats), name="header", size=3),
            Layout(predictions_table(db, "stock", "📈 Stock Predictions"), name="main"),
            Layout(name="bottom", size=10),
        )
        layout["bottom"].split_row(
            Layout(accuracy_panel(db, "stock", "Stock Accuracy"), ratio=2),
            Layout(stats_panel(db, cfg),                          ratio=1),
        )

    return layout


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(prog="dashboard")
    parser.add_argument(
        "--mode",
        choices=["crypto", "stock", "both"],
        default="both",
        help="Which universe to display (default: both)",
    )
    args = parser.parse_args()

    cfg = Config()
    db  = BotDB(cfg.db_path, history_limit=cfg.history_limit)
    refresh = int(os.getenv("DASHBOARD_REFRESH", "5"))

    do_crypto = args.mode in ("crypto", "both")
    do_stocks = args.mode in ("stock",  "both")

    console.print(f"[bold cyan]trade-bot dashboard[/] mode=[yellow]{args.mode}[/] — refreshing every {refresh}s  Ctrl+C to quit")
    try:
        with Live(render(db, cfg, do_crypto, do_stocks), refresh_per_second=1, screen=True) as live:
            while True:
                time.sleep(refresh)
                live.update(render(db, cfg, do_crypto, do_stocks))
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard closed.[/]")


if __name__ == "__main__":
    main()

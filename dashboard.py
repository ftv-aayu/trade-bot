#!/usr/bin/env python3
"""Terminal dashboard for trade-bot. Reads live data from the SQLite DB."""
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
    from rich import box
except ImportError:
    print("Install 'rich' to run the dashboard: pip install rich")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.db import BotDB

IST = ZoneInfo("Asia/Kolkata")
console = Console()


def make_header(cfg: Config, stats: dict) -> Panel:
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    prices = stats.get("prices_rows", 0)
    preds = stats.get("predictions_rows", 0)
    text = Text()
    text.append(f" trade-bot  ", style="bold white on dark_blue")
    text.append(f" {now} ", style="cyan")
    text.append(f" DB: {prices:,} prices | {preds:,} predictions ", style="dim")
    return Panel(text, style="blue")


def make_predictions_table(db: BotDB) -> Table:
    rows = db.recent_predictions(limit=20)
    table = Table(
        title="Recent Predictions",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Symbol", style="bold white", width=12)
    table.add_column("Predicted Ret", justify="right", style="green", width=14)
    table.add_column("Confidence", justify="right", width=11)
    table.add_column("P(Up)", justify="right", style="green", width=8)
    table.add_column("P(Down)", justify="right", style="red", width=8)
    table.add_column("Model", justify="center", width=7)
    table.add_column("Time", style="dim", width=20)

    seen: set[str] = set()
    for row in rows:
        sym = str(row.get("symbol", ""))
        if sym in seen:
            continue
        seen.add(sym)
        pred_ret = float(row.get("predicted_return", 0))
        conf = float(row.get("confidence", 0))
        p_up = float(row.get("probability_up", 0))
        p_down = float(row.get("probability_down", 0))
        model_on = bool(row.get("model_enabled", 0))
        ts = int(row.get("timestamp_ms", 0))
        time_str = datetime.fromtimestamp(ts / 1000, tz=IST).strftime("%H:%M:%S") if ts else "-"

        pred_color = "green" if pred_ret > 0 else "red"
        table.add_row(
            sym,
            Text(f"{pred_ret:+.4%}", style=pred_color),
            f"{conf:.2%}",
            f"{p_up:.2%}",
            f"{p_down:.2%}",
            Text("✓", style="green") if model_on else Text("—", style="dim"),
            time_str,
        )
    return table


def make_accuracy_panel(db: BotDB) -> Panel:
    acc = db.prediction_accuracy()
    n = acc.get("n", 0)
    da = acc.get("directional_accuracy", 0.0)
    if n == 0:
        content = Text("No settled predictions yet. Predictions settle after the horizon passes.", style="dim")
    else:
        content = Text()
        content.append(f"Settled predictions: ", style="dim")
        content.append(f"{n:,}\n", style="bold white")
        content.append(f"Directional accuracy: ", style="dim")
        da_color = "green" if da > 0.52 else "yellow" if da > 0.50 else "red"
        content.append(f"{da:.2%}\n", style=da_color)
    return Panel(content, title="Model Accuracy", border_style="green")


def make_stats_panel(db: BotDB, cfg: Config) -> Panel:
    stats = db.stats()
    counts = db.history_counts(list(cfg.watch_symbols[:10]))
    content = Text()
    content.append("Price rows: ", style="dim")
    content.append(f"{stats.get('prices_rows', 0):,}\n", style="white")
    content.append("Prediction rows: ", style="dim")
    content.append(f"{stats.get('predictions_rows', 0):,}\n", style="white")
    content.append("\nHistory depth (top symbols):\n", style="dim")
    for sym, cnt in sorted(counts.items(), key=lambda x: -x[1])[:5]:
        bar_len = min(20, cnt // 50)
        bar = "█" * bar_len
        content.append(f"  {sym:<12} ", style="cyan")
        content.append(f"{cnt:>5} bars ", style="white")
        content.append(f"{bar}\n", style="green")
    return Panel(content, title="Database Stats", border_style="blue")


def render(db: BotDB, cfg: Config) -> Layout:
    stats = db.stats()
    layout = Layout()
    layout.split_column(
        Layout(make_header(cfg, stats), size=3),
        Layout(name="body"),
        Layout(make_accuracy_panel(db), size=8),
    )
    layout["body"].split_row(
        Layout(make_predictions_table(db), ratio=3),
        Layout(make_stats_panel(db, cfg), ratio=1),
    )
    return layout


def main() -> None:
    cfg = Config()
    db = BotDB(cfg.db_path, history_limit=cfg.history_limit)
    refresh = int(os.getenv("DASHBOARD_REFRESH", "5"))
    console.print(f"[bold cyan]trade-bot dashboard[/] — refreshing every {refresh}s. Press Ctrl+C to quit.")
    try:
        with Live(render(db, cfg), refresh_per_second=1, screen=True) as live:
            while True:
                time.sleep(refresh)
                live.update(render(db, cfg))
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard closed.[/]")


if __name__ == "__main__":
    main()

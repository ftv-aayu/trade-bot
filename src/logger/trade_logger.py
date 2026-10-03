"""
Trade Logger
Logs every order and periodically writes a performance summary.
"""

import csv
import json
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"
LOGS_DIR.mkdir(exist_ok=True)

TRADE_LOG = LOGS_DIR / "trades.csv"
PERF_LOG = LOGS_DIR / "performance.jsonl"


def _now_iso() -> str:
    return datetime.now(IST).isoformat()


class TradeLogger:
    def __init__(self):
        self._ensure_trade_log_header()
        self._portfolio_snapshots: list[dict] = []

    # ------------------------------------------------------------------
    # Trade logging
    # ------------------------------------------------------------------

    def _ensure_trade_log_header(self):
        if not TRADE_LOG.exists():
            with open(TRADE_LOG, "w", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp", "pair", "side", "type", "quantity",
                        "price", "order_id", "status", "commission_usd", "note",
                    ],
                )
                writer.writeheader()

    def log_order(self, order_response: dict, note: str = ""):
        """Log a filled or pending order from the API response."""
        detail = order_response.get("OrderDetail", {})
        if not detail:
            # short open/close response shape
            detail = order_response

        row = {
            "timestamp": _now_iso(),
            "pair": detail.get("Pair", ""),
            "side": detail.get("Side", ""),
            "type": detail.get("Type", ""),
            "quantity": detail.get("FilledQuantity") or detail.get("Quantity") or detail.get("ShortQty", ""),
            "price": detail.get("FilledAverPrice") or detail.get("Price") or detail.get("EntryPrice", ""),
            "order_id": detail.get("OrderID") or detail.get("ID", ""),
            "status": detail.get("Status", ""),
            "commission_usd": detail.get("CommissionChargeValue") or detail.get("OpenFee", ""),
            "note": note,
        }

        with open(TRADE_LOG, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            writer.writerow(row)

        logger.info(
            "TRADE %s %s %s qty=%s @ %s [%s]",
            row["pair"], row["side"], row["type"],
            row["quantity"], row["price"], row["status"],
        )

    def log_error(self, context: str, error_msg: str):
        """Log an API or strategy error."""
        logger.error("[%s] %s", context, error_msg)

    # ------------------------------------------------------------------
    # Portfolio snapshots and performance
    # ------------------------------------------------------------------

    def snapshot(self, portfolio_usd: float, extra: dict = None):
        """Record a portfolio value snapshot."""
        entry = {
            "timestamp": _now_iso(),
            "portfolio_usd": round(portfolio_usd, 4),
        }
        if extra:
            entry.update(extra)
        self._portfolio_snapshots.append(entry)

        with open(PERF_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def performance_summary(self, initial: float, current: float) -> dict:
        """Compute basic performance metrics from snapshots."""
        if len(self._portfolio_snapshots) < 2:
            return {"status": "insufficient data"}

        values = [s["portfolio_usd"] for s in self._portfolio_snapshots]
        returns = [
            (values[i + 1] - values[i]) / values[i]
            for i in range(len(values) - 1)
            if values[i] != 0
        ]

        if not returns:
            return {"status": "no returns yet"}

        import math

        total_return = (current - initial) / initial
        avg_return = sum(returns) / len(returns)
        std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5

        downside = [r for r in returns if r < 0]
        downside_std = (sum(r ** 2 for r in downside) / len(downside)) ** 0.5 if downside else 1e-9

        peak = initial
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

        sharpe = (avg_return / std_return) * math.sqrt(len(returns)) if std_return > 0 else 0.0
        sortino = (avg_return / downside_std) * math.sqrt(len(returns)) if downside_std > 0 else 0.0
        calmar = total_return / max_dd if max_dd > 0 else 0.0

        summary = {
            "total_return_pct": round(total_return * 100, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "calmar_ratio": round(calmar, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "num_snapshots": len(values),
        }
        logger.info("Performance: %s", summary)
        return summary

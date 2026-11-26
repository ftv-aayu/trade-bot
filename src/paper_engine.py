"""src/paper_engine.py — shared paper trading engine.

Handles virtual wallets, positions, order history, and P&L for both
paper_arena.py (manual trading) and auto_trader.py (bot-driven trading).
All state persisted in the main SQLite DB under separate tables.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_wallets (
    trader_id   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    qty         REAL NOT NULL DEFAULT 0,
    avg_cost    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (trader_id, symbol)
);
CREATE TABLE IF NOT EXISTS paper_cash (
    trader_id   TEXT PRIMARY KEY,
    cash        REAL NOT NULL DEFAULT 100000,
    created_ms  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trader_id   TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    qty         REAL NOT NULL,
    price       REAL NOT NULL,
    notional    REAL NOT NULL,
    fee         REAL NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_trader ON paper_orders(trader_id, timestamp_ms);
CREATE TABLE IF NOT EXISTS paper_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trader_id   TEXT NOT NULL,
    start_ms    INTEGER NOT NULL,
    end_ms      INTEGER,
    start_cash  REAL NOT NULL,
    end_cash    REAL,
    pnl_pct     REAL,
    score       REAL,
    note        TEXT NOT NULL DEFAULT ''
);
"""

FEE_RATE = 0.001   # 0.1% taker fee (simulated)
STARTING_CASH = 100_000.0


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float
    current_price: float

    @property
    def value(self) -> float:
        return self.qty * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return self.qty * (self.current_price - self.avg_cost)

    @property
    def unrealized_pnl_pct(self) -> float:
        return (self.current_price / self.avg_cost - 1.0) * 100 if self.avg_cost > 0 else 0.0


@dataclass
class WalletSnapshot:
    trader_id: str
    cash: float
    positions: list[Position]
    total_value: float
    unrealized_pnl: float
    pnl_pct: float
    starting_cash: float = STARTING_CASH

    @property
    def positions_value(self) -> float:
        return sum(p.value for p in self.positions)


class PaperEngine:
    """Shared engine for all paper trading operations."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()   # serialize all writes
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=60)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=60000")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _write(self, sql: str, params=()) -> None:
        """Execute a single write statement under the write lock."""
        with self._write_lock:
            self._conn().execute(sql, params)
            self._conn().commit()

    def _writemany(self, sql: str, rows: list) -> None:
        """Execute a batch write under the write lock."""
        if not rows:
            return
        with self._write_lock:
            self._conn().executemany(sql, rows)
            self._conn().commit()

    def _init_schema(self) -> None:
        with self._write_lock:
            self._conn().executescript(PAPER_SCHEMA)
            self._conn().commit()

    # ------------------------------------------------------------------
    # Trader registration
    # ------------------------------------------------------------------

    def register(self, trader_id: str, starting_cash: float = STARTING_CASH) -> bool:
        """Create a new trader wallet. Returns False if already exists."""
        trader_id = str(trader_id).strip() if trader_id else ""
        if not trader_id:
            return False
        exists = self._conn().execute(
            "SELECT 1 FROM paper_cash WHERE trader_id=?", (trader_id,)
        ).fetchone()
        if exists:
            return False
        with self._write_lock:
            self._conn().execute(
                "INSERT INTO paper_cash(trader_id,cash,created_ms) VALUES(?,?,?)",
                (trader_id, float(starting_cash), _now_ms()),
            )
            self._conn().commit()
        return True

    def reset(self, trader_id: str, starting_cash: float = STARTING_CASH) -> None:
        """Reset a trader back to starting cash, wiping all positions and orders."""
        with self._write_lock:
            self._conn().execute("DELETE FROM paper_wallets WHERE trader_id=?", (trader_id,))
            self._conn().execute(
                "INSERT OR REPLACE INTO paper_cash(trader_id,cash,created_ms) VALUES(?,?,?)",
                (trader_id, float(starting_cash), _now_ms()),
            )
            self._conn().commit()

    # ------------------------------------------------------------------
    # Trading operations
    # ------------------------------------------------------------------

    def buy(self, trader_id: str, symbol: str, usdt_amount: float,
            current_price: float, note: str = "") -> dict:
        """Buy `usdt_amount` worth of `symbol` at `current_price`."""
        self.register(trader_id)
        with self._write_lock:
            cash = self._get_cash(trader_id)
            usdt_amount = min(float(usdt_amount), cash)
            if usdt_amount <= 0 or current_price <= 0:
                return {"ok": False, "error": "Invalid amount or price"}
            fee   = usdt_amount * FEE_RATE
            qty   = (usdt_amount - fee) / current_price

            self._conn().execute(
                "UPDATE paper_cash SET cash=cash-? WHERE trader_id=?",
                (usdt_amount, trader_id),
            )
            existing = self._conn().execute(
                "SELECT qty, avg_cost FROM paper_wallets WHERE trader_id=? AND symbol=?",
                (trader_id, symbol),
            ).fetchone()
            if existing:
                old_qty  = float(existing["qty"])
                old_cost = float(existing["avg_cost"])
                new_qty  = old_qty + qty
                new_cost = (old_qty * old_cost + qty * current_price) / new_qty
                self._conn().execute(
                    "UPDATE paper_wallets SET qty=?, avg_cost=? WHERE trader_id=? AND symbol=?",
                    (new_qty, new_cost, trader_id, symbol),
                )
            else:
                self._conn().execute(
                    "INSERT INTO paper_wallets(trader_id,symbol,qty,avg_cost) VALUES(?,?,?,?)",
                    (trader_id, symbol, qty, current_price),
                )
            self._conn().execute(
                "INSERT INTO paper_orders(trader_id,timestamp_ms,symbol,side,qty,price,notional,fee,note)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (trader_id, _now_ms(), symbol, "BUY", qty, current_price, usdt_amount, fee, note),
            )
            self._conn().commit()
        return {"ok": True, "symbol": symbol, "qty": qty, "price": current_price,
                "spent": usdt_amount, "fee": fee}

    def sell(self, trader_id: str, symbol: str, qty_or_pct: float,
             current_price: float, is_pct: bool = False, note: str = "") -> dict:
        """Sell `qty_or_pct` of `symbol`. If is_pct=True, treats as fraction of holding (0–1)."""
        with self._write_lock:
            pos = self._conn().execute(
                "SELECT qty, avg_cost FROM paper_wallets WHERE trader_id=? AND symbol=?",
                (trader_id, symbol),
            ).fetchone()
            if not pos or float(pos["qty"]) <= 0:
                return {"ok": False, "error": f"No position in {symbol}"}
            held     = float(pos["qty"])
            qty      = held * min(1.0, float(qty_or_pct)) if is_pct else min(float(qty_or_pct), held)
            if qty <= 0 or current_price <= 0:
                return {"ok": False, "error": "Invalid qty or price"}
            notional     = qty * current_price
            fee          = notional * FEE_RATE
            proceeds     = notional - fee
            realized_pnl = qty * (current_price - float(pos["avg_cost"])) - fee
            new_qty      = held - qty

            if new_qty < 1e-9:
                self._conn().execute(
                    "DELETE FROM paper_wallets WHERE trader_id=? AND symbol=?", (trader_id, symbol)
                )
            else:
                self._conn().execute(
                    "UPDATE paper_wallets SET qty=? WHERE trader_id=? AND symbol=?",
                    (new_qty, trader_id, symbol),
                )
            self._conn().execute(
                "UPDATE paper_cash SET cash=cash+? WHERE trader_id=?", (proceeds, trader_id)
            )
            self._conn().execute(
                "INSERT INTO paper_orders(trader_id,timestamp_ms,symbol,side,qty,price,notional,fee,note)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (trader_id, _now_ms(), symbol, "SELL", qty, current_price, notional, fee, note),
            )
            self._conn().commit()
        return {"ok": True, "symbol": symbol, "qty": qty, "price": current_price,
                "proceeds": proceeds, "fee": fee, "realized_pnl": realized_pnl}

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def snapshot(self, trader_id: str, current_prices: dict[str, float]) -> WalletSnapshot:
        self.register(trader_id)
        cash = self._get_cash(trader_id)
        rows = self._conn().execute(
            "SELECT symbol, qty, avg_cost FROM paper_wallets WHERE trader_id=? AND qty>0",
            (trader_id,),
        ).fetchall()
        positions = []
        for r in rows:
            sym = str(r["symbol"])
            price = float(current_prices.get(sym, r["avg_cost"]))
            positions.append(Position(sym, float(r["qty"]), float(r["avg_cost"]), price))
        total = cash + sum(p.value for p in positions)
        upnl = sum(p.unrealized_pnl for p in positions)
        pnl_pct = (total - STARTING_CASH) / STARTING_CASH * 100
        return WalletSnapshot(trader_id, cash, positions, total, upnl, pnl_pct)

    def order_history(self, trader_id: str, limit: int = 50) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM paper_orders WHERE trader_id=? ORDER BY timestamp_ms DESC LIMIT ?",
            (trader_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_traders(self) -> list[str]:
        rows = self._conn().execute(
            "SELECT trader_id FROM paper_cash WHERE trader_id IS NOT NULL AND trader_id != '' ORDER BY trader_id"
        ).fetchall()
        return [r["trader_id"] for r in rows]

    def leaderboard(self, current_prices: dict[str, float]) -> list[dict]:
        traders = self.all_traders()
        board = []
        for tid in traders:
            snap = self.snapshot(tid, current_prices)
            board.append({
                "trader_id": tid,
                "total_value": snap.total_value,
                "pnl_pct": snap.pnl_pct,
                "cash": snap.cash,
                "positions": len(snap.positions),
            })
        board.sort(key=lambda x: x["total_value"], reverse=True)
        return board

    # ------------------------------------------------------------------
    # Session scoring (for cross-dev EduMind integration)
    # ------------------------------------------------------------------

    def score_session(self, trader_id: str, current_prices: dict[str, float]) -> dict:
        """
        Score a trading session 0–1 based on:
          - 50% P&L vs starting cash
          - 30% diversification (number of different symbols traded)
          - 20% discipline (used stop-loss / sold losers)
        """
        snap = self.snapshot(trader_id, current_prices)
        orders = self.order_history(trader_id, limit=200)

        # P&L component (capped at ±20% → maps to 0–1)
        pnl_score = min(1.0, max(0.0, (snap.pnl_pct + 20) / 40))

        # Diversification: 5+ unique symbols traded = max score
        unique_syms = len({o["symbol"] for o in orders})
        div_score = min(1.0, unique_syms / 5)

        # Discipline: fraction of sells that locked in a loss (took a stop-loss)
        sells = [o for o in orders if o["side"] == "SELL"]
        buys_map: dict[str, float] = {}
        for o in sorted(orders, key=lambda x: x["timestamp_ms"]):
            if o["side"] == "BUY":
                buys_map[o["symbol"]] = float(o["price"])
        disciplined = sum(
            1 for s in sells
            if s["symbol"] in buys_map and float(s["price"]) < buys_map[s["symbol"]]
        )
        disc_score = min(1.0, disciplined / max(len(sells), 1))

        total_score = round(0.50 * pnl_score + 0.30 * div_score + 0.20 * disc_score, 3)

        return {
            "trader_id": trader_id,
            "total_value": round(snap.total_value, 2),
            "pnl_pct": round(snap.pnl_pct, 2),
            "score": total_score,
            "pnl_component": round(pnl_score, 3),
            "diversification_component": round(div_score, 3),
            "discipline_component": round(disc_score, 3),
            "unique_symbols_traded": unique_syms,
            "total_orders": len(orders),
        }

    def _get_cash(self, trader_id: str) -> float:
        row = self._conn().execute(
            "SELECT cash FROM paper_cash WHERE trader_id=?", (trader_id,)
        ).fetchone()
        return float(row["cash"]) if row else STARTING_CASH

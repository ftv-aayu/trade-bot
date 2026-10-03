"""
src/db.py
SQLite database for storing prices, trades, equity, and API events.
Thread-safe -- uses a single connection with WAL mode.
"""

import sqlite3
import threading
import time
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "bot.sqlite3"

SCHEMA = """
    CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS prices (
        timestamp_ms INTEGER NOT NULL,
        pair TEXT NOT NULL,
        price REAL NOT NULL,
        bid REAL NOT NULL,
        ask REAL NOT NULL,
        change_24h REAL NOT NULL,
        unit_trade_value REAL NOT NULL,
        spread_bps REAL NOT NULL,
        PRIMARY KEY(timestamp_ms, pair)
    );
    CREATE INDEX IF NOT EXISTS idx_prices_pair_ts ON prices(pair, timestamp_ms);
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_ms INTEGER NOT NULL,
        pair TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity REAL NOT NULL,
        price REAL NOT NULL,
        notional REAL NOT NULL,
        fee REAL NOT NULL,
        mode TEXT NOT NULL,
        order_id TEXT,
        status TEXT NOT NULL,
        session_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp_ms);
    CREATE TABLE IF NOT EXISTS equity (
        timestamp_ms INTEGER PRIMARY KEY,
        equity REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS api_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_ms INTEGER NOT NULL,
        endpoint TEXT NOT NULL,
        success INTEGER NOT NULL,
        message TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_api_events_ts ON api_events(timestamp_ms);
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_ms INTEGER NOT NULL,
        pair TEXT NOT NULL,
        price REAL NOT NULL,
        target_time_ms INTEGER NOT NULL,
        predicted_return REAL NOT NULL,
        probability_up REAL NOT NULL,
        probability_flat REAL NOT NULL,
        probability_down REAL NOT NULL,
        confidence REAL NOT NULL,
        model_enabled INTEGER NOT NULL,
        realized_return REAL,
        realized_state INTEGER,
        settled INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_time_ms, settled);
    CREATE INDEX IF NOT EXISTS idx_predictions_pair ON predictions(pair, timestamp_ms);
"""


def _ts() -> int:
    """Current time as 13-digit millisecond timestamp."""
    return int(time.time() * 1000)


class BotDB:
    def __init__(self, db_path: str = None):
        self._path = str(db_path or DB_PATH)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._local = threading.local()
        self._init_schema()
        # Generate a unique session ID for this run
        self.session_id = str(int(time.time()))
        logger.info("Database ready: %s (session=%s)", self._path, self.session_id)

    def _conn(self) -> sqlite3.Connection:
        """Return a thread-local connection, creating it if needed."""
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads
            conn.execute("PRAGMA synchronous=NORMAL") # safe + fast
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self):
        conn = self._conn()
        conn.executescript(SCHEMA)
        conn.commit()
        # Migration: add session_id column to trades if it doesn't exist yet
        # (handles DBs created before this column was added to the schema)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "session_id" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN session_id TEXT")
            conn.commit()
            logger.info("Migration: added session_id column to trades table")

    # -- state ---------------------------------------------------------

    def set_state(self, key: str, value: str):
        self._conn().execute(
            "INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)",
            (key, value)
        )
        self._conn().commit()

    def get_state(self, key: str, default: str = None) -> str:
        row = self._conn().execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    # -- prices --------------------------------------------------------

    def insert_prices(self, tickers: dict, timestamp_ms: int = None):
        """
        Bulk insert ticker snapshot.
        tickers = { 'BTC/USD': { MaxBid, MinAsk, LastPrice, Change,
                                  CoinTradeValue, UnitTradeValue }, ... }
        """
        ts = timestamp_ms or _ts()
        rows = []
        for pair, t in tickers.items():
            bid   = t.get("MaxBid", 0.0)
            ask   = t.get("MinAsk", 0.0)
            price = t.get("LastPrice", 0.0)
            spread_bps = ((ask - bid) / price * 10000) if price > 0 else 0.0
            rows.append((
                ts,
                pair,
                price,
                bid,
                ask,
                t.get("Change", 0.0),
                t.get("UnitTradeValue", 0.0),
                round(spread_bps, 4),
            ))
        if rows:
            self._conn().executemany(
                """INSERT OR IGNORE INTO prices
                   (timestamp_ms, pair, price, bid, ask,
                    change_24h, unit_trade_value, spread_bps)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self._conn().commit()

    def get_prices(self, pair: str, limit: int = 100) -> list[dict]:
        rows = self._conn().execute(
            """SELECT * FROM prices WHERE pair = ?
               ORDER BY timestamp_ms DESC LIMIT ?""",
            (pair, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- trades --------------------------------------------------------

    def insert_trade(
        self,
        pair: str,
        side: str,
        quantity: float,
        price: float,
        fee: float,
        mode: str,
        order_id: str = None,
        status: str = "FILLED",
        timestamp_ms: int = None,
    ):
        ts       = timestamp_ms or _ts()
        notional = quantity * price
        self._conn().execute(
            """INSERT INTO trades
               (timestamp_ms, pair, side, quantity, price,
                notional, fee, mode, order_id, status, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, pair, side.upper(), quantity, price,
             notional, fee, mode, order_id, status, self.session_id),
        )
        self._conn().commit()

    def insert_trade_from_order(self, order_result: dict, mode: str):
        """Insert a trade from a place_order() API/paper response."""
        d = order_result.get("OrderDetail", order_result)
        pair     = d.get("Pair", "")
        side     = d.get("Side", "")
        qty      = float(d.get("FilledQuantity") or d.get("Quantity") or d.get("ShortQty") or 0)
        price    = float(d.get("FilledAverPrice") or d.get("Price") or d.get("EntryPrice") or 0)
        fee      = float(d.get("CommissionChargeValue") or d.get("OpenFee") or 0)
        order_id = str(d.get("OrderID") or d.get("ID") or "")
        status   = d.get("Status", "FILLED")
        ts       = d.get("CreateTimestamp") or _ts()
        if pair and qty and price:
            self.insert_trade(pair, side, qty, price, fee, mode, order_id, status, ts)

    def get_trades(self, limit: int = 50) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM trades ORDER BY timestamp_ms DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_trades(self, limit: int = 200) -> list[dict]:
        """Return only trades from the current session."""
        rows = self._conn().execute(
            "SELECT * FROM trades WHERE session_id = ? ORDER BY timestamp_ms DESC LIMIT ?",
            (self.session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- equity --------------------------------------------------------

    def insert_equity(self, equity: float, timestamp_ms: int = None):
        ts = timestamp_ms or _ts()
        self._conn().execute(
            "INSERT OR REPLACE INTO equity(timestamp_ms, equity) VALUES (?, ?)",
            (ts, round(equity, 4)),
        )
        self._conn().commit()

    def get_equity_history(self, limit: int = 200) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM equity ORDER BY timestamp_ms DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- api_events ----------------------------------------------------

    def log_api_event(
        self,
        endpoint: str,
        success: bool,
        message: str = "",
        timestamp_ms: int = None,
    ):
        ts = timestamp_ms or _ts()
        self._conn().execute(
            """INSERT INTO api_events(timestamp_ms, endpoint, success, message)
               VALUES (?, ?, ?, ?)""",
            (ts, endpoint, int(success), message),
        )
        self._conn().commit()

    # -- quick stats ---------------------------------------------------

    def stats(self) -> dict:
        conn = self._conn()
        price_count  = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        trade_count  = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        equity_count = conn.execute("SELECT COUNT(*) FROM equity").fetchone()[0]
        event_count  = conn.execute("SELECT COUNT(*) FROM api_events").fetchone()[0]
        latest_eq    = conn.execute(
            "SELECT equity FROM equity ORDER BY timestamp_ms DESC LIMIT 1"
        ).fetchone()
        return {
            "price_rows":   price_count,
            "trade_rows":   trade_count,
            "equity_rows":  equity_count,
            "api_events":   event_count,
            "latest_equity": latest_eq[0] if latest_eq else None,
        }

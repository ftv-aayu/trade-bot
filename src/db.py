from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prices (
    timestamp_ms INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    change_pct_24h REAL NOT NULL,
    quote_volume_24h REAL NOT NULL,
    PRIMARY KEY(timestamp_ms, symbol)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol_ts ON prices(symbol, timestamp_ms);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    symbol TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_pred_target ON predictions(target_time_ms, settled);
CREATE INDEX IF NOT EXISTS idx_pred_sym_ts ON predictions(symbol, timestamp_ms);
CREATE TABLE IF NOT EXISTS api_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    endpoint TEXT NOT NULL,
    success INTEGER NOT NULL,
    message TEXT NOT NULL
);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


class BotDB:
    def __init__(self, path: str = "data/trade_bot.sqlite3", history_limit: int = 2000):
        self.path = str(path)
        self.history_limit = int(history_limit)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.session_id = str(int(time.time()))
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        self._conn().executescript(SCHEMA)
        self._conn().commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

    def get_state(self, key: str, default=None):
        row = self._conn().execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return row[0]

    def set_state(self, key: str, value) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
            (key, json.dumps(value, separators=(",", ":")))
        )
        self._conn().commit()

    def add_price_snapshots(self, timestamp_ms: int, snapshots: list[dict]) -> None:
        """snapshots: list of Binance ticker dicts with symbol, lastPrice, bidPrice, askPrice, etc."""
        rows = []
        for t in snapshots:
            sym = str(t.get("symbol", ""))
            price = float(t.get("lastPrice", 0) or 0)
            if not sym or price <= 0:
                continue
            bid = float(t.get("bidPrice", price) or price)
            ask = float(t.get("askPrice", price) or price)
            change = float(t.get("priceChangePercent", 0) or 0) / 100.0
            vol = float(t.get("quoteVolume", 0) or 0)
            rows.append((int(timestamp_ms), sym, price, bid, ask, change, vol))
        if rows:
            self._conn().executemany(
                "INSERT OR IGNORE INTO prices(timestamp_ms,symbol,price,bid,ask,change_pct_24h,quote_volume_24h) VALUES(?,?,?,?,?,?,?)",
                rows,
            )
            self._conn().commit()

    def add_kline_prices(self, symbol: str, klines: list[tuple[int, float]]) -> None:
        """klines: list of (timestamp_ms, close_price) tuples."""
        rows = [
            (int(ts), str(symbol), float(price), float(price), float(price), 0.0, 0.0)
            for ts, price in klines if float(price) > 0
        ]
        if rows:
            self._conn().executemany(
                "INSERT OR IGNORE INTO prices(timestamp_ms,symbol,price,bid,ask,change_pct_24h,quote_volume_24h) VALUES(?,?,?,?,?,?,?)",
                rows,
            )
            self._conn().commit()

    def histories(self, symbols: list[str], limit: int | None = None) -> dict[str, list[float]]:
        if not symbols:
            return {}
        limit = int(limit or self.history_limit)
        placeholders = ",".join("?" for _ in symbols)
        sql = f"""
            SELECT symbol, price FROM (
                SELECT symbol, price, timestamp_ms,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp_ms DESC) AS rn
                FROM prices WHERE symbol IN ({placeholders})
            ) WHERE rn <= ?
            ORDER BY symbol ASC, timestamp_ms ASC
        """
        rows = self._conn().execute(sql, [*symbols, limit]).fetchall()
        result = {s: [] for s in symbols}
        for row in rows:
            result[row["symbol"]].append(float(row["price"]))
        return result

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        rows = self._conn().execute(
            f"SELECT symbol, price FROM prices WHERE (symbol, timestamp_ms) IN "
            f"(SELECT symbol, MAX(timestamp_ms) FROM prices WHERE symbol IN ({placeholders}) GROUP BY symbol)",
            symbols,
        ).fetchall()
        return {row["symbol"]: float(row["price"]) for row in rows}

    def history_counts(self, symbols: list[str]) -> dict[str, int]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        rows = self._conn().execute(
            f"SELECT symbol, COUNT(*) as cnt FROM prices WHERE symbol IN ({placeholders}) GROUP BY symbol",
            symbols,
        ).fetchall()
        return {row["symbol"]: int(row["cnt"]) for row in rows}

    def record_prediction(self, timestamp_ms: int, symbol: str, price: float, target_time_ms: int,
                          predicted_return: float, probability_up: float, probability_flat: float,
                          probability_down: float, confidence: float, model_enabled: bool) -> None:
        self._conn().execute(
            """INSERT INTO predictions(timestamp_ms,symbol,price,target_time_ms,predicted_return,
               probability_up,probability_flat,probability_down,confidence,model_enabled)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (int(timestamp_ms), symbol, float(price), int(target_time_ms), float(predicted_return),
             float(probability_up), float(probability_flat), float(probability_down), float(confidence),
             int(bool(model_enabled))),
        )
        self._conn().commit()

    def settle_predictions(self, now_ms: int, current_prices: Mapping[str, float], class_threshold: float) -> int:
        rows = self._conn().execute(
            "SELECT id,symbol,price,predicted_return,target_time_ms FROM predictions WHERE settled=0 AND target_time_ms<=? LIMIT 5000",
            (int(now_ms),),
        ).fetchall()
        updates = []
        for row in rows:
            current = float(current_prices.get(row["symbol"], 0) or 0)
            if current <= 0 or float(row["price"]) <= 0:
                continue
            realized = current / float(row["price"]) - 1.0
            state = 1 if realized > class_threshold else -1 if realized < -class_threshold else 0
            updates.append((realized, state, row["id"]))
        if updates:
            self._conn().executemany(
                "UPDATE predictions SET realized_return=?,realized_state=?,settled=1 WHERE id=?", updates
            )
            self._conn().commit()
        return len(updates)

    def recent_predictions(self, limit: int = 50) -> list[dict]:
        rows = self._conn().execute(
            "SELECT symbol,timestamp_ms,predicted_return,probability_up,probability_flat,probability_down,"
            "confidence,model_enabled FROM predictions ORDER BY timestamp_ms DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def prediction_accuracy(self) -> dict:
        rows = self._conn().execute(
            "SELECT predicted_return,realized_return FROM predictions WHERE settled=1"
        ).fetchall()
        if not rows:
            return {"n": 0, "directional_accuracy": 0.0}
        import numpy as np
        pred = [float(r[0]) for r in rows]
        real = [float(r[1]) for r in rows]
        p = np.where(np.array(pred) >= 0, 1, -1)
        r = np.where(np.array(real) >= 0, 1, -1)
        return {"n": len(rows), "directional_accuracy": float(np.mean(p == r))}

    def record_api_event(self, timestamp_ms: int, endpoint: str, success: bool, message: str = "") -> None:
        self._conn().execute(
            "INSERT INTO api_events(timestamp_ms,endpoint,success,message) VALUES(?,?,?,?)",
            (int(timestamp_ms), endpoint, int(bool(success)), message),
        )
        self._conn().commit()

    def stats(self) -> dict:
        conn = self._conn()
        result = {}
        for table in ("prices", "predictions", "api_events"):
            result[f"{table}_rows"] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return result

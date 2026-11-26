"""arena_server.py — FastAPI backend for the Paper Trading Practice Arena plugin.

This server is what EduMind (and any other cross-dev partner) talks to.
It exposes:
  - REST endpoints for trading, portfolio, scores, predictions, leaderboard
  - A webhook receiver for EduMind events (mastery.updated etc.)
  - Swagger docs at /docs (auto-generated)

Run:
    python arena_server.py               # default port 8000
    python arena_server.py --port 8080

Or via uvicorn directly:
    uvicorn arena_server:app --reload --port 8000
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=False)

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.config import Config
from src.db import BotDB
from src.paper_engine import PaperEngine, STARTING_CASH
from src.api.stock_client import is_market_open, minutes_until_open

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("arena_server")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Paper Trading Practice Arena",
    description=(
        "Plugin API for the Paper Trading Practice Arena. "
        "Provides virtual wallets, live paper trading, ML predictions, "
        "leaderboard, and session scoring for integration with EduMind."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # restrict to EduMind's origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy singletons — created on first request
_cfg: Config | None = None
_db: BotDB | None = None
_engine: PaperEngine | None = None


def get_cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg


def get_db() -> BotDB:
    global _db
    if _db is None:
        cfg = get_cfg()
        _db = BotDB(cfg.db_path, history_limit=cfg.history_limit)
    return _db


def get_engine() -> PaperEngine:
    global _engine
    if _engine is None:
        cfg = get_cfg()
        _engine = PaperEngine(cfg.db_path)
    return _engine


# ---------------------------------------------------------------------------
# Live price helper (same as paper_arena.py — API first, DB fallback)
# ---------------------------------------------------------------------------

_binance_client = None
_stock_client = None


def _live_prices() -> dict[str, float]:
    global _binance_client, _stock_client
    cfg = get_cfg()
    db  = get_db()
    prices: dict[str, float] = {}

    try:
        from src.api.client import BinanceClient
        if _binance_client is None:
            _binance_client = BinanceClient(timeout_seconds=10.0, min_request_interval=0.0)
        tickers = _binance_client.get_all_tickers_map()
        for sym in cfg.watch_symbols:
            t = tickers.get(sym)
            if t:
                p = float(t.get("lastPrice", 0) or 0)
                if p > 0:
                    prices[sym] = p
    except Exception as exc:
        logger.warning("Binance price fetch failed: %s", exc)

    if cfg.stocks_enabled and cfg.watch_stocks:
        try:
            from src.api.stock_client import StockClient
            if _stock_client is None:
                _stock_client = StockClient(min_request_interval=0.1)
            prices.update(_stock_client.fetch_latest_prices(list(cfg.watch_stocks)))
        except Exception as exc:
            logger.warning("Yahoo price fetch failed: %s", exc)

    missing = [s for s in list(cfg.watch_symbols) + list(cfg.watch_stocks) if s not in prices]
    if missing:
        prices.update(db.latest_prices(missing))

    return prices


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    trader_id: str = Field(..., description="Unique student/trader ID, e.g. 's_12345' or 'Alice'")
    starting_cash: float = Field(STARTING_CASH, description="Starting virtual cash (default $100,000)")


class BuyRequest(BaseModel):
    trader_id: str = Field(..., description="Trader ID")
    symbol: str    = Field(..., description="Symbol to buy, e.g. 'BTCUSDT' or 'NVDA'")
    amount_usd: float = Field(..., description="USD amount to spend", gt=0)


class SellRequest(BaseModel):
    trader_id: str  = Field(..., description="Trader ID")
    symbol: str     = Field(..., description="Symbol to sell")
    percent: float  = Field(100.0, description="What % of the holding to sell (1–100)", ge=1, le=100)


class EduMindEvent(BaseModel):
    event: str                     = Field(..., description="Event type, e.g. 'mastery.updated'")
    student_id: str                = Field(..., description="EduMind student ID")
    timestamp: Optional[str]       = Field(None, description="ISO-8601 timestamp")
    changes: Optional[dict]        = Field(None, description="For mastery.updated: {topic: {old, new}}")
    recommended_topics: Optional[list[str]] = Field(None, description="For recommendations.updated")


# ---------------------------------------------------------------------------
# Health / info
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
def root():
    """Health check."""
    return {
        "service":      "Paper Trading Practice Arena",
        "status":       "ok",
        "time_ist":     datetime.now(IST).isoformat(),
        "market_open":  is_market_open(),
        "docs":         "/docs",
    }


@app.get("/health", tags=["Health"])
def health():
    """Detailed health check — DB stats and market status."""
    db  = get_db()
    cfg = get_cfg()
    stats = db.stats()
    return {
        "status":            "ok",
        "prices_in_db":      stats.get("prices_rows", 0),
        "predictions_in_db": stats.get("predictions_rows", 0),
        "us_market_open":    is_market_open(),
        "mins_until_open":   minutes_until_open(),
        "crypto_symbols":    len(cfg.watch_symbols),
        "stock_tickers":     len(cfg.watch_stocks),
    }


# ---------------------------------------------------------------------------
# Market data endpoints
# ---------------------------------------------------------------------------

@app.get("/prices", tags=["Market Data"])
def get_prices(symbol: Optional[str] = None):
    """
    Get live prices for all watched symbols, or a specific one.

    - **symbol**: optional filter, e.g. `BTCUSDT` or `AAPL`
    """
    prices = _live_prices()
    if symbol:
        sym = symbol.upper()
        if sym not in prices:
            raise HTTPException(404, f"Symbol '{sym}' not found or not watched")
        return {"symbol": sym, "price": prices[sym], "timestamp": datetime.now(IST).isoformat()}
    return {
        "prices":    prices,
        "count":     len(prices),
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get("/predictions", tags=["Market Data"])
def get_predictions(asset_type: Optional[str] = None, limit: int = 40):
    """
    Get the latest ML predictions from the trading engine.

    - **asset_type**: `crypto`, `stock`, or omit for both
    - **limit**: max rows to return (default 40)

    Returns the most recent prediction per symbol.
    """
    db = get_db()
    rows = db.recent_predictions(limit=limit * 3, asset_type=asset_type)
    seen: dict[str, dict] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in seen:
            seen[sym] = {
                "symbol":           sym,
                "asset_type":       r.get("asset_type", "crypto"),
                "predicted_return": round(float(r["predicted_return"]), 6),
                "probability_up":   round(float(r["probability_up"]), 4),
                "probability_flat": round(float(r["probability_flat"]), 4),
                "probability_down": round(float(r["probability_down"]), 4),
                "confidence":       round(float(r["confidence"]), 4),
                "model_enabled":    bool(r["model_enabled"]),
                "timestamp":        datetime.fromtimestamp(r["timestamp_ms"]/1000, tz=IST).isoformat(),
                "signal":           "BUY" if float(r["predicted_return"]) > 0.0005 and float(r["confidence"]) >= 0.18
                                    else "SELL" if float(r["predicted_return"]) < -0.0005 and float(r["confidence"]) >= 0.18
                                    else "HOLD",
            }
    result = list(seen.values())[:limit]
    return {"predictions": result, "count": len(result)}


@app.get("/predictions/{symbol}", tags=["Market Data"])
def get_prediction_for_symbol(symbol: str):
    """Get the latest ML prediction for a specific symbol."""
    db   = get_db()
    rows = db.recent_predictions(limit=100)
    sym  = symbol.upper()
    for r in rows:
        if r["symbol"] == sym:
            return {
                "symbol":           sym,
                "asset_type":       r.get("asset_type", "crypto"),
                "predicted_return": round(float(r["predicted_return"]), 6),
                "probability_up":   round(float(r["probability_up"]), 4),
                "probability_flat": round(float(r["probability_flat"]), 4),
                "probability_down": round(float(r["probability_down"]), 4),
                "confidence":       round(float(r["confidence"]), 4),
                "model_enabled":    bool(r["model_enabled"]),
                "signal":           "BUY" if float(r["predicted_return"]) > 0.0005 else
                                    "SELL" if float(r["predicted_return"]) < -0.0005 else "HOLD",
                "timestamp":        datetime.fromtimestamp(r["timestamp_ms"]/1000, tz=IST).isoformat(),
            }
    raise HTTPException(404, f"No predictions found for '{sym}'")


@app.get("/accuracy", tags=["Market Data"])
def get_accuracy():
    """Get the ML model's live directional accuracy on settled predictions."""
    db = get_db()
    return {
        "crypto": db.prediction_accuracy("crypto"),
        "stock":  db.prediction_accuracy("stock"),
        "overall": db.prediction_accuracy(),
    }


# ---------------------------------------------------------------------------
# Trader / wallet endpoints
# ---------------------------------------------------------------------------

@app.post("/traders/register", tags=["Trading"])
def register_trader(req: RegisterRequest):
    """
    Register a new trader and create their virtual wallet.
    If the trader already exists, returns their current portfolio.
    """
    engine = get_engine()
    prices = _live_prices()
    is_new = engine.register(req.trader_id, req.starting_cash)
    snap   = engine.snapshot(req.trader_id, prices)
    return {
        "trader_id":     req.trader_id,
        "is_new":        is_new,
        "cash":          round(snap.cash, 2),
        "total_value":   round(snap.total_value, 2),
        "starting_cash": req.starting_cash,
    }


@app.get("/traders/{trader_id}/portfolio", tags=["Trading"])
def get_portfolio(trader_id: str):
    """
    Get a trader's current portfolio at live prices.

    Returns cash, positions with live P&L, total value, and overall P&L %.
    """
    engine = get_engine()
    prices = _live_prices()
    engine.register(trader_id)
    snap   = engine.snapshot(trader_id, prices)
    return {
        "trader_id":      trader_id,
        "cash":           round(snap.cash, 2),
        "total_value":    round(snap.total_value, 2),
        "positions_value":round(snap.positions_value, 2),
        "unrealized_pnl": round(snap.unrealized_pnl, 2),
        "pnl_pct":        round(snap.pnl_pct, 4),
        "starting_cash":  STARTING_CASH,
        "positions": [
            {
                "symbol":             p.symbol,
                "qty":                round(p.qty, 6),
                "avg_cost":           round(p.avg_cost, 6),
                "current_price":      round(p.current_price, 6),
                "value":              round(p.value, 2),
                "unrealized_pnl":     round(p.unrealized_pnl, 2),
                "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 4),
            }
            for p in snap.positions
        ],
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.post("/traders/buy", tags=["Trading"])
def buy(req: BuyRequest):
    """
    Buy a symbol with a USD amount at the current live price.

    - Fills immediately at the live market price.
    - A 0.1% simulated taker fee is deducted.
    - Never sends any order to Binance — paper only.
    """
    engine = get_engine()
    prices = _live_prices()
    sym    = req.symbol.upper()

    if sym not in prices:
        raise HTTPException(400, f"Symbol '{sym}' not found or price unavailable")

    price  = prices[sym]
    result = engine.buy(req.trader_id, sym, req.amount_usd, price, note="api")

    if not result["ok"]:
        raise HTTPException(400, result.get("error", "Order failed"))

    return {
        "ok":        True,
        "trader_id": req.trader_id,
        "side":      "BUY",
        "symbol":    sym,
        "qty":       round(result["qty"], 6),
        "price":     round(price, 6),
        "spent_usd": round(result["spent"], 2),
        "fee_usd":   round(result["fee"], 2),
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.post("/traders/sell", tags=["Trading"])
def sell(req: SellRequest):
    """
    Sell a % of a held position at the current live price.

    - **percent**: 1–100. Default 100 (close full position).
    - Fills immediately at live market price.
    """
    engine = get_engine()
    prices = _live_prices()
    sym    = req.symbol.upper()

    if sym not in prices:
        raise HTTPException(400, f"Symbol '{sym}' not found or price unavailable")

    price  = prices[sym]
    result = engine.sell(req.trader_id, sym, req.percent / 100.0,
                         price, is_pct=True, note="api")

    if not result["ok"]:
        raise HTTPException(400, result.get("error", "Sell failed"))

    return {
        "ok":           True,
        "trader_id":    req.trader_id,
        "side":         "SELL",
        "symbol":       sym,
        "qty":          round(result["qty"], 6),
        "price":        round(price, 6),
        "proceeds_usd": round(result["proceeds"], 2),
        "fee_usd":      round(result["fee"], 2),
        "realized_pnl": round(result["realized_pnl"], 2),
        "timestamp":    datetime.now(IST).isoformat(),
    }


@app.get("/traders/{trader_id}/orders", tags=["Trading"])
def get_orders(trader_id: str, limit: int = 50):
    """Get order history for a trader (most recent first)."""
    engine = get_engine()
    orders = engine.order_history(trader_id, limit=limit)
    return {
        "trader_id": trader_id,
        "orders": [
            {
                "id":          o["id"],
                "side":        o["side"],
                "symbol":      o["symbol"],
                "qty":         round(float(o["qty"]), 6),
                "price":       round(float(o["price"]), 6),
                "notional":    round(float(o["notional"]), 2),
                "fee":         round(float(o["fee"]), 4),
                "timestamp":   datetime.fromtimestamp(o["timestamp_ms"]/1000, tz=IST).isoformat(),
                "note":        o.get("note", ""),
            }
            for o in orders
        ],
        "count": len(orders),
    }


@app.get("/traders/{trader_id}/score", tags=["Trading"])
def get_score(trader_id: str):
    """
    Get the session score for a trader (0–1).

    Score breakdown:
    - **50%** P&L vs starting cash
    - **30%** Diversification (unique symbols traded)
    - **20%** Discipline (cut losing positions)

    This is the payload to POST to EduMind's quiz-results endpoint.
    """
    engine = get_engine()
    prices = _live_prices()
    score  = engine.score_session(trader_id, prices)
    return {
        **score,
        "total_value":    round(score["total_value"], 2),
        "pnl_pct":        round(score["pnl_pct"], 4),
        "edumind_payload": {
            "plugin_id":   "paper-trading-arena",
            "student_id":  trader_id,
            "session_id":  f"sess_{int(time.time())}",
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "topic":       "risk_management",
            "score":       score["score"],
            "max_score":   1.0,
            "breakdown": {
                "pnl_component":              score["pnl_component"],
                "diversification_component":  score["diversification_component"],
                "discipline_component":       score["discipline_component"],
            },
            "details": {
                "starting_cash":         STARTING_CASH,
                "ending_value":          round(score["total_value"], 2),
                "pnl_pct":               round(score["pnl_pct"], 4),
                "unique_symbols_traded": score["unique_symbols_traded"],
                "total_orders":          score["total_orders"],
            },
        },
    }


@app.post("/traders/{trader_id}/reset", tags=["Trading"])
def reset_trader(trader_id: str, starting_cash: float = STARTING_CASH):
    """Reset a trader back to starting cash, wiping all positions and orders."""
    engine = get_engine()
    engine.reset(trader_id, starting_cash)
    return {"ok": True, "trader_id": trader_id, "cash": starting_cash}


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

@app.get("/leaderboard", tags=["Leaderboard"])
def leaderboard():
    """
    Get the live leaderboard across all registered traders.
    Sorted by total portfolio value descending.
    """
    engine = get_engine()
    prices = _live_prices()
    board  = engine.leaderboard(prices)
    return {
        "leaderboard": [
            {
                "rank":        i + 1,
                "trader_id":   row["trader_id"],
                "total_value": round(row["total_value"], 2),
                "pnl_pct":     round(row["pnl_pct"], 4),
                "cash":        round(row["cash"], 2),
                "positions":   row["positions"],
            }
            for i, row in enumerate(board)
        ],
        "count":     len(board),
        "timestamp": datetime.now(IST).isoformat(),
    }


# ---------------------------------------------------------------------------
# EduMind webhook receiver
# ---------------------------------------------------------------------------

_received_events: list[dict] = []   # in-memory log of received events


@app.post("/webhooks/edumind", tags=["Webhooks"])
async def edumind_webhook(event: EduMindEvent, request: Request):
    """
    Receive webhook events from EduMind.

    Supported events:
    - **mastery.updated**: a student's topic mastery changed
    - **recommendations.updated**: EduMind recommends new practice topics

    Always responds within 1s with `{"received": true}`.
    """
    payload = {
        "event":      event.event,
        "student_id": event.student_id,
        "timestamp":  event.timestamp or datetime.now(timezone.utc).isoformat(),
        "received_at":datetime.now(IST).isoformat(),
        "data":       {
            "changes":              event.changes,
            "recommended_topics":   event.recommended_topics,
        },
    }
    _received_events.append(payload)
    if len(_received_events) > 200:
        _received_events.pop(0)

    logger.info("EduMind webhook: %s for student %s", event.event, event.student_id)

    # If mastery.updated, auto-register the student so they have a wallet ready
    if event.event == "mastery.updated":
        engine = get_engine()
        engine.register(event.student_id)

    return {"received": True, "event": event.event, "student_id": event.student_id}


@app.get("/webhooks/edumind/log", tags=["Webhooks"])
def webhook_log(limit: int = 20):
    """Get the last N received webhook events (for debugging)."""
    return {
        "events": _received_events[-limit:],
        "total_received": len(_received_events),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser(prog="arena_server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on file changes (dev mode)")
    args = parser.parse_args()

    print(f"\n  Paper Trading Practice Arena")
    print(f"  API:   http://{args.host}:{args.port}")
    print(f"  Docs:  http://localhost:{args.port}/docs")
    print(f"  Press Ctrl+C to stop\n")

    uvicorn.run(
        "arena_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()

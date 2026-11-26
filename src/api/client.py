from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATA_BASE_URL = "https://data-api.binance.vision"
API_BASE_URL = "https://api.binance.com"


class BinanceClient:
    """Public market data client using Binance's no-auth data endpoint."""

    def __init__(self, timeout_seconds: float = 15.0, min_request_interval: float = 0.1):
        self.base_url = DATA_BASE_URL
        self.timeout = max(1.0, float(timeout_seconds))
        self.min_request_interval = max(0.0, float(min_request_interval))
        self._last_request = 0.0
        self.session = requests.Session()
        # Increase pool size to handle concurrent access from bot + arena server
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": "trade-bot/1.0"})

    def _pace(self) -> None:
        wait = self.min_request_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        self._pace()
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params or {}, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("GET %s failed: %s", path, exc)
            return {}

    def ping(self) -> bool:
        """Return True if the server is reachable."""
        result = self._get("/api/v3/ping")
        return isinstance(result, dict)

    def server_time(self) -> int:
        """Return server time in milliseconds."""
        data = self._get("/api/v3/time")
        return int(data.get("serverTime", 0)) if isinstance(data, dict) else 0

    def exchange_info(self, symbols: list[str] | None = None) -> dict:
        """
        Return exchange info. If symbols is provided, filters to those symbols.
        Each symbol in the returned 'symbols' list has 'symbol', 'status', 'filters'.
        """
        params: dict = {}
        if symbols:
            # Binance accepts multiple symbols as ["BTCUSDT","ETHUSDT"]
            import json
            params["symbols"] = json.dumps(symbols)
        data = self._get("/api/v3/exchangeInfo", params)
        return data if isinstance(data, dict) else {}

    def ticker_24hr(self, symbol: str | None = None) -> list[dict]:
        """
        Return 24hr ticker statistics.
        Returns a list of dicts with: symbol, lastPrice, bidPrice, askPrice,
        priceChangePercent, quoteVolume, highPrice, lowPrice.
        """
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/api/v3/ticker/24hr", params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "symbol" in data:
            return [data]
        return []

    def klines(self, symbol: str, interval: str = "1m", limit: int = 1000,
               start_time_ms: int | None = None, end_time_ms: int | None = None) -> list[list]:
        """
        Return kline/candlestick data.
        Each kline is: [openTime, open, high, low, close, volume, closeTime,
                        quoteAssetVolume, numTrades, takerBuyBaseVol, takerBuyQuoteVol, ignore]
        Close price is at index 4.
        interval: 1s,1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M
        limit: max 1000 per request
        """
        params: dict = {"symbol": symbol, "interval": interval, "limit": min(int(limit), 1000)}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        data = self._get("/api/v3/klines", params)
        return data if isinstance(data, list) else []

    def klines_close_prices(self, symbol: str, interval: str = "1m", limit: int = 1000) -> list[float]:
        """Convenience: return only the close prices as floats."""
        raw = self.klines(symbol, interval=interval, limit=limit)
        return [float(k[4]) for k in raw if len(k) > 4]

    def get_price(self, symbol: str) -> float | None:
        """Return the latest price for a symbol, or None on failure."""
        data = self._get("/api/v3/ticker/price", {"symbol": symbol})
        if isinstance(data, dict) and "price" in data:
            return float(data["price"])
        return None

    def get_all_tickers_map(self) -> dict[str, dict]:
        """
        Return a dict of symbol -> ticker dict for all USDT pairs.
        Filters to symbols ending in USDT only.
        """
        tickers = self.ticker_24hr()
        return {
            t["symbol"]: t
            for t in tickers
            if isinstance(t, dict) and str(t.get("symbol", "")).endswith("USDT")
        }

    def get_tradable_usdt_symbols(self, min_quote_volume: float = 1_000_000.0) -> list[str]:
        """
        Return a sorted list of USDT spot symbols that are TRADING and
        have a 24h quote volume above min_quote_volume.
        """
        info = self.exchange_info()
        trading = {
            s["symbol"]
            for s in info.get("symbols", [])
            if s.get("status") == "TRADING" and str(s.get("symbol", "")).endswith("USDT")
               and s.get("quoteAsset") == "USDT"
        }
        tickers = self.ticker_24hr()
        result = []
        for t in tickers:
            sym = str(t.get("symbol", ""))
            if sym not in trading:
                continue
            vol = float(t.get("quoteVolume", 0) or 0)
            if vol >= min_quote_volume:
                result.append(sym)
        return sorted(result)

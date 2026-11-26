"""Yahoo Finance stock data client via yfinance.

No API key required. Returns the same (timestamp_ms, close_price) tuples
that the Binance client returns, so the rest of the bot is data-source-agnostic.

Key constraints from Yahoo Finance:
  - 1m interval: max 7 days of history within last 30 days
  - 5m interval: max 60 days of history  <-- we use this
  - Market hours: Mon–Fri 9:30am–4:00pm US/Eastern (plus pre/post market)
  - Rate limiting: polite delays between requests recommended
"""
from __future__ import annotations

import logging
import time
import warnings
# yfinance uses deprecated pandas inplace= API internally — suppress the noise
warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")
warnings.filterwarnings("ignore", message=".*inplace.*deprecated.*")
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 30
_MARKET_CLOSE_HOUR = 16


def _us_holidays(year: int) -> set[tuple[int, int]]:
    """Return a set of (month, day) tuples for US stock market holidays in the given year."""
    from datetime import date

    def nth_weekday(year, month, weekday, n):
        """Return the nth weekday (0=Mon) of the given month."""
        d = date(year, month, 1)
        days_ahead = weekday - d.weekday()
        if days_ahead < 0:
            days_ahead += 7
        d += __import__('datetime').timedelta(days=days_ahead + 7 * (n - 1))
        return d

    holidays = set()
    # New Year's Day (Jan 1, observed on Mon if Sun)
    ny = date(year, 1, 1)
    if ny.weekday() == 6: ny = date(year, 1, 2)
    holidays.add((ny.month, ny.day))
    # MLK Day (3rd Monday of January)
    mlk = nth_weekday(year, 1, 0, 3)
    holidays.add((mlk.month, mlk.day))
    # Presidents Day (3rd Monday of February)
    pres = nth_weekday(year, 2, 0, 3)
    holidays.add((pres.month, pres.day))
    # Good Friday (Friday before Easter — approximate)
    # Easter calculation (Gregorian)
    a, b, c = year % 19, year // 100, year % 100
    d2, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d2 - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = __import__('datetime').date(year, month, day)
    good_friday = easter - __import__('datetime').timedelta(days=2)
    holidays.add((good_friday.month, good_friday.day))
    # Memorial Day (last Monday of May)
    mem = nth_weekday(year, 5, 0, 4)
    while mem.month == 6:
        mem -= __import__('datetime').timedelta(days=7)
    # Actually: last Monday in May
    d3 = date(year, 6, 1) - __import__('datetime').timedelta(days=1)
    while d3.weekday() != 0:
        d3 -= __import__('datetime').timedelta(days=1)
    holidays.add((d3.month, d3.day))
    # Juneteenth (June 19, observed Mon if Sun)
    jun = date(year, 6, 19)
    if jun.weekday() == 6: jun = date(year, 6, 20)
    if jun.weekday() == 5: jun = date(year, 6, 18)
    holidays.add((jun.month, jun.day))
    # Independence Day (July 4, observed Mon if Sun, Fri if Sat)
    ind = date(year, 7, 4)
    if ind.weekday() == 6: ind = date(year, 7, 5)
    if ind.weekday() == 5: ind = date(year, 7, 3)
    holidays.add((ind.month, ind.day))
    # Labor Day (1st Monday of September)
    lab = nth_weekday(year, 9, 0, 1)
    holidays.add((lab.month, lab.day))
    # Thanksgiving (4th Thursday of November)
    tg = nth_weekday(year, 11, 3, 4)
    holidays.add((tg.month, tg.day))
    # Christmas (Dec 25, observed Mon if Sun, Fri if Sat)
    xmas = date(year, 12, 25)
    if xmas.weekday() == 6: xmas = date(year, 12, 26)
    if xmas.weekday() == 5: xmas = date(year, 12, 24)
    holidays.add((xmas.month, xmas.day))
    return holidays


def is_market_open(now: datetime | None = None) -> bool:
    """Return True if US stock market is currently open (Mon–Fri, 9:30–16:00 ET, non-holiday)."""
    now = now or datetime.now(tz=ET)
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return False
    if (et.month, et.day) in _us_holidays(et.year):
        return False
    open_time  = et.replace(hour=_MARKET_OPEN_HOUR,  minute=_MARKET_OPEN_MINUTE, second=0, microsecond=0)
    close_time = et.replace(hour=_MARKET_CLOSE_HOUR, minute=0,                   second=0, microsecond=0)
    return open_time <= et < close_time


def minutes_until_open(now: datetime | None = None) -> int:
    """Return minutes until next market open (0 if already open)."""
    import math
    now = (now or datetime.now(tz=ET)).astimezone(ET)
    if is_market_open(now):
        return 0
    # Find next weekday 9:30 ET
    candidate = now.replace(hour=_MARKET_OPEN_HOUR, minute=_MARKET_OPEN_MINUTE, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate.replace(day=candidate.day + 1)
    while candidate.weekday() >= 5:
        candidate = candidate.replace(day=candidate.day + 1)
    delta = (candidate - now).total_seconds()
    return max(0, int(math.ceil(delta / 60)))


class StockClient:
    """Thin wrapper around yfinance for fetching stock OHLCV data."""

    def __init__(self, min_request_interval: float = 0.5):
        self.min_request_interval = max(0.0, float(min_request_interval))
        self._last_request = 0.0

    def _pace(self) -> None:
        wait = self.min_request_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def fetch_history(
        self,
        ticker: str,
        interval: str = "5m",
        period: str = "60d",
    ) -> list[tuple[int, float]]:
        """Return (timestamp_ms, close_price) tuples, oldest first.

        interval options: 1m (7d max), 5m (60d max), 15m, 30m, 1h, 1d
        period options:   7d, 60d, 1mo, 3mo, 6mo, 1y, max
        """
        self._pace()
        try:
            import yfinance as yf
            df = yf.download(
                ticker,
                interval=interval,
                period=period,
                progress=False,
                auto_adjust=True,
                prepost=False,   # regular trading hours only
            )
            if df is None or df.empty:
                logger.warning("yfinance: no data for %s", ticker)
                return []
            # Flatten MultiIndex columns if present (yfinance batch download)
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            close_col = None
            for col in ("Close", "close", "Adj Close"):
                if col in df.columns:
                    close_col = col
                    break
            if close_col is None:
                logger.warning("yfinance: no Close column for %s — columns: %s", ticker, list(df.columns))
                return []
            result = []
            for idx, row in df.iterrows():
                price = float(row[close_col])
                if price <= 0:
                    continue
                # Convert index (timezone-aware Timestamp) to ms
                ts = int(idx.timestamp() * 1000)
                result.append((ts, price))
            return result
        except Exception as exc:
            logger.error("yfinance fetch_history %s: %s", ticker, exc)
            return []

    def fetch_latest_price(self, ticker: str) -> float | None:
        """Return the most recent close price for a ticker."""
        self._pace()
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            # fast_info is cheaper than history for a single price
            price = getattr(t.fast_info, "last_price", None)
            if price and float(price) > 0:
                return float(price)
            # Fallback: 1-day 1m history
            hist = self.fetch_history(ticker, interval="1m", period="1d")
            return hist[-1][1] if hist else None
        except Exception as exc:
            logger.error("yfinance fetch_latest_price %s: %s", ticker, exc)
            return None

    def fetch_latest_prices(self, tickers: list[str]) -> dict[str, float]:
        """Return {ticker: price} for a list of tickers using a batch download."""
        if not tickers:
            return {}
        self._pace()
        try:
            import yfinance as yf
            df = yf.download(
                tickers,
                period="1d",
                interval="1m",
                progress=False,
                auto_adjust=True,
                prepost=False,
                group_by="ticker",
            )
            if df is None or df.empty:
                return {}
            result: dict[str, float] = {}
            if len(tickers) == 1:
                # Single ticker: df has flat columns
                close_col = next((c for c in df.columns if str(c).lower() in ("close", "adj close")), None)
                if close_col and not df.empty:
                    val = float(df[close_col].dropna().iloc[-1])
                    if val > 0:
                        result[tickers[0]] = val
            else:
                for ticker in tickers:
                    try:
                        close = df[ticker]["Close"].dropna()
                        if not close.empty:
                            val = float(close.iloc[-1])
                            if val > 0:
                                result[ticker] = val
                    except (KeyError, IndexError):
                        pass
            return result
        except Exception as exc:
            logger.error("yfinance fetch_latest_prices batch: %s", exc)
            return {}

    def build_market_snapshot(
        self,
        ticker: str,
        price: float,
        prev_close: float | None = None,
    ) -> dict:
        """Build a market dict in the same shape add_price_snapshots() expects."""
        change = (price / prev_close - 1.0) if prev_close and prev_close > 0 else 0.0
        return {
            "symbol": ticker,
            "lastPrice": price,
            "bidPrice": price,
            "askPrice": price,
            "priceChangePercent": change * 100.0,
            "quoteVolume": 0.0,
            "asset_type": "stock",
        }

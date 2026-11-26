from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(x.strip().upper() for x in os.getenv(name, default).split(",") if x.strip())


def minutes_to_bars(minutes: int | float, poll_seconds: int) -> int:
    return max(1, int(round(float(minutes) * 60.0 / max(int(poll_seconds), 1))))


@dataclass(frozen=True)
class Config:
    # Binance public data endpoint - no API key required
    binance_base_url: str = os.getenv("BINANCE_BASE_URL", "https://data-api.binance.vision")

    bot_namespace: str = os.getenv("BOT_NAMESPACE", "trade-bot-v1")

    # Polling and scheduling
    poll_seconds: int = _int("POLL_SECONDS", 60)
    kline_interval: str = os.getenv("KLINE_INTERVAL", "1m")
    timeout_seconds: float = _float("TIMEOUT_SECONDS", 15.0)
    history_limit: int = _int("HISTORY_LIMIT", 2000)
    predictor_history_limit: int = _int("PREDICTOR_HISTORY_LIMIT", 25000)
    exchange_info_refresh_seconds: int = _int("EXCHANGE_INFO_REFRESH_SECONDS", 900)
    prediction_retrain_seconds: int = _int("PREDICTION_RETRAIN_SECONDS", 1800)
    predictor_retrain_seconds: int = _int("PREDICTION_RETRAIN_SECONDS", 1800)  # alias for boss.py
    prediction_log_interval_seconds: int = _int("PREDICTION_LOG_INTERVAL_SECONDS", 300)
    api_min_request_interval_seconds: float = _float("API_MIN_REQUEST_INTERVAL_SECONDS", 0.10)

    # Symbols — crypto (Binance USDT pairs)
    # 100 liquid, well-known symbols — large enough for cross-sectional ML metrics
    # (rank IC needs 50+ observations per timestamp to be statistically meaningful)
    watch_symbols: tuple[str, ...] = _csv("WATCH_SYMBOLS",
        "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,TRXUSDT,DOTUSDT,"
        "LINKUSDT,LTCUSDT,NEARUSDT,UNIUSDT,ATOMUSDT,XLMUSDT,ETCUSDT,BCHUSDT,ALGOUSDT,VETUSDT,"
        "FILUSDT,APTUSDT,ARBUSDT,OPUSDT,INJUSDT,SUIUSDT,SEIUSDT,TIAUSDT,FETUSDT,RENDERUSDT,"
        "AAVEUSDT,CRVUSDT,LDOUSDT,MKRUSDT,SNXUSDT,COMPUSDT,SUSHIUSDT,BALUSDT,YFIUSDT,CAKEUSDT,"
        "SANDUSDT,MANAUSDT,AXSUSDT,ENJUSDT,GALAUSDT,CHZUSDT,FLOWUSDT,IMXUSDT,XTZUSDT,WLDUSDT,"
        "PEPEUSDT,SHIBUSDT,FLOKIUSDT,HYPEUSDT,TAOUSDT,ENAUSDT,ONDOUSDT,STXUSDT,ICPUSDT,RUNEUSDT,"
        "KAVAUSDT,BANDUSDT,ANKRUSDT,COTIUSDT,STORJUSDT,SKLUSDT,HBARUSDT,QNTUSDT,EGLDUSDT,ZILUSDT,"
        "IOSTUSDT,ONTUSDT,ZENUSDT,DCRUSDT,LRCUSDT,RAYUSDT,POLUSDT,STRKUSDT,BLURUSDT,CELOUSDT,"
        "LPTUSDT,NKNUSDT,XVGUSDT,BURGERUSDT,PONDUSDT,TLMUSDT,ALPHAUSDT,CTKUSDT,XVSUSDT,CELRUSDT,"
        "ZRXUSDT,MTLUSDT,MANTRAUSDT,MOVEUSDT,PENGUUSDT,VIRTUALUSDT,ORDIUSDT,BONKUSDT,TRUMPUSDT,SSVUSDT"
    )
    min_quote_volume: float = _float("MIN_QUOTE_VOLUME", 5_000_000.0)
    top_n: int = _int("TOP_N", 20)
    diagnostic_top_n: int = _int("DIAGNOSTIC_TOP_N", 50)

    # Symbols — stocks (Yahoo Finance tickers)
    watch_stocks: tuple[str, ...] = _csv("WATCH_STOCKS",
        "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,JPM,V,UNH,"
        "XOM,JNJ,WMT,PG,MA,HD,CVX,ABBV,MRK,COST")
    stock_interval: str = os.getenv("STOCK_INTERVAL", "5m")  # 5m = up to 60d history
    stocks_enabled: bool = _bool("STOCKS_ENABLED", True)
    stock_poll_seconds: int = _int("STOCK_POLL_SECONDS", 300)  # poll stocks every 5 min

    # Strategy windows (in minutes)
    fast_window: int = _int("FAST_WINDOW", 60)
    medium_window: int = _int("MEDIUM_WINDOW", 240)
    long_window: int = _int("LONG_WINDOW", 720)
    trend_window: int = _int("TREND_WINDOW", 360)
    vol_window: int = _int("VOL_WINDOW", 60)
    breakout_window: int = _int("BREAKOUT_WINDOW", 240)

    # Long signal filters
    min_score: float = _float("MIN_SCORE", 0.51)
    min_relative_edge: float = _float("MIN_RELATIVE_EDGE", 0.0025)
    min_1h_return: float = _float("MIN_1H_RETURN", -0.010)
    min_4h_return: float = _float("MIN_4H_RETURN", -0.010)
    min_12h_return: float = _float("MIN_12H_RETURN", -0.020)
    max_spread_bps: float = _float("MAX_SPREAD_BPS", 20.0)
    commission_rate: float = _float("TAKER_FEE_ASSUMPTION", 0.001)

    # Short signal filters (shorts_enabled=False disables them in the strategy)
    shorts_enabled: bool = _bool("SHORTS_ENABLED", False)
    allow_shorts_risk_on: bool = _bool("ALLOW_SHORTS_RISK_ON", False)
    min_short_score: float = _float("MIN_SHORT_SCORE", 0.53)
    min_short_relative_edge: float = _float("MIN_SHORT_RELATIVE_EDGE", 0.0025)
    max_short_1h_return: float = _float("MAX_SHORT_1H_RETURN", 0.0020)
    max_short_4h_return: float = _float("MAX_SHORT_4H_RETURN", -0.0020)
    max_short_12h_return: float = _float("MAX_SHORT_12H_RETURN", 0.0030)

    # Short sizing
    short_top_n: int = _int("SHORT_TOP_N", 10)
    max_short_gross_target: float = _float("MAX_SHORT_GROSS_TARGET", 0.42)
    max_short_asset_weight: float = _float("MAX_SHORT_ASSET_WEIGHT", 0.075)
    short_strength_power: float = _float("SHORT_STRENGTH_POWER", 1.25)

    # Portfolio gross target and cash
    max_total_gross_target: float = _float("MAX_TOTAL_GROSS_TARGET", 0.98)
    cash_reserve: float = _float("CASH_RESERVE", 0.02)

    # Symbol whitelist (empty = all watched symbols allowed)
    allow_pairs: tuple[str, ...] = _csv("ALLOW_PAIRS", "")

    # Regime
    defensive_min_breadth: float = _float("DEFENSIVE_MIN_BREADTH", 0.08)
    defensive_min_strength: float = _float("DEFENSIVE_MIN_STRENGTH", 0.12)
    risk_on_gross_cap: float = _float("RISK_ON_GROSS_CAP", 1.00)
    balanced_gross_cap: float = _float("BALANCED_GROSS_CAP", 0.90)
    risk_off_gross_cap: float = _float("RISK_OFF_GROSS_CAP", 0.75)
    defensive_gross_cap: float = _float("DEFENSIVE_GROSS_CAP", 0.50)
    gross_strength_power: float = _float("GROSS_STRENGTH_POWER", 1.15)
    allocation_edge_power: float = _float("ALLOCATION_EDGE_POWER", 1.10)
    allocation_vol_power: float = _float("ALLOCATION_VOL_POWER", 0.75)

    # Portfolio optimizer
    max_gross_target: float = _float("MAX_GROSS_TARGET", 0.70)
    max_asset_weight: float = _float("MAX_ASSET_WEIGHT", 0.10)
    portfolio_risk_aversion: float = _float("PORTFOLIO_RISK_AVERSION", 60.0)
    portfolio_cost_penalty: float = _float("PORTFOLIO_COST_PENALTY", 1.0)
    portfolio_covariance_window_minutes: int = _int("PORTFOLIO_COVARIANCE_WINDOW_MINUTES", 360)
    portfolio_covariance_half_life_minutes: int = _int("PORTFOLIO_COVARIANCE_HALF_LIFE_MINUTES", 180)
    portfolio_risk_horizon_minutes: int = _int("PORTFOLIO_RISK_HORIZON_MINUTES", 60)
    portfolio_covariance_shrinkage: float = _float("PORTFOLIO_COVARIANCE_SHRINKAGE", 0.65)
    portfolio_optimizer_iterations: int = _int("PORTFOLIO_OPTIMIZER_ITERATIONS", 250)
    correlation_window: int = _int("CORRELATION_WINDOW", 240)
    max_pair_correlation: float = _float("MAX_PAIR_CORRELATION", 0.90)
    correlation_penalty: float = _float("CORRELATION_PENALTY", 0.12)
    overextension_penalty: float = _float("OVEREXTENSION_PENALTY", 0.06)

    # Predictor
    predictor_enabled: bool = _bool("PREDICTOR_ENABLED", True)
    prediction_horizon: int = _int("PREDICTION_HORIZON", 60)
    predictor_feature_window: int = _int("PREDICTOR_FEATURE_WINDOW", 360)
    predictor_min_samples: int = _int("PREDICTOR_MIN_SAMPLES", 5000)
    predictor_max_samples: int = _int("PREDICTOR_MAX_SAMPLES", 60_000)
    predictor_ridge_alpha: float = _float("PREDICTOR_RIDGE_ALPHA", 4.0)
    predictor_classifier_alpha: float = _float("PREDICTOR_CLASSIFIER_ALPHA", 6.0)
    predictor_validation_fraction: float = _float("PREDICTOR_VALIDATION_FRACTION", 0.25)
    predictor_purge_gap: int = _int("PREDICTOR_PURGE_GAP", 60)
    # Gate thresholds.
    # With 100 symbols, cross-sectional metrics (rank_ic, top_decile_edge)
    # have 100 observations per timestamp — statistically meaningful.
    # These are now genuine quality gates, not noise filters.
    predictor_min_directional_accuracy: float = _float("PREDICTOR_MIN_DIRECTIONAL_ACCURACY", 0.47)
    predictor_min_correlation: float          = _float("PREDICTOR_MIN_CORRELATION",           -0.05)
    predictor_min_rank_ic: float              = _float("PREDICTOR_MIN_RANK_IC",               -0.05)
    predictor_min_balanced_accuracy: float    = _float("PREDICTOR_MIN_BALANCED_ACCURACY",     0.33)
    predictor_min_top_decile_edge: float      = _float("PREDICTOR_MIN_TOP_DECILE_EDGE",       -0.002)
    predictor_min_top_decile_net_return: float= _float("PREDICTOR_MIN_TOP_DECILE_NET_RETURN", -0.006)
    slippage_bps: float = _float("SLIPPAGE_BPS", 10.0)
    predictor_max_brier: float = _float("PREDICTOR_MAX_BRIER", 0.245)
    predictor_class_threshold: float = _float("PREDICTOR_CLASS_THRESHOLD", 0.0015)
    bearish_veto_enabled: bool = _bool("PREDICTOR_BEARISH_VETO_ENABLED", True)
    bearish_veto_return: float = _float("PREDICTOR_BEARISH_VETO_RETURN", -0.005)
    bearish_veto_probability: float = _float("PREDICTOR_BEARISH_VETO_PROBABILITY", 0.60)
    bearish_veto_confidence: float = _float("PREDICTOR_BEARISH_VETO_CONFIDENCE", 0.60)
    target_hourly_vol: float = _float("TARGET_HOURLY_VOL", 0.010)
    prediction_log_top_n: int = _int("PREDICTION_LOG_TOP_N", 100)

    # Paths
    log_file: str = os.getenv("LOG_FILE", "logs/bot.log")
    db_path: str = os.getenv("BOT_DB_PATH", "data/trade_bot.sqlite3")

    def bars(self, minutes: int | float) -> int:
        return minutes_to_bars(minutes, self.poll_seconds)

    def validate(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)
        if self.poll_seconds < 1:
            raise ValueError("POLL_SECONDS must be at least 1")
        if not (2 <= self.fast_window < self.medium_window < self.long_window):
            raise ValueError("Require FAST_WINDOW < MEDIUM_WINDOW < LONG_WINDOW")
        if not (0.10 <= self.predictor_validation_fraction < 0.50):
            raise ValueError("Invalid validation fraction")
        if self.predictor_min_samples < 200:
            raise ValueError("PREDICTOR_MIN_SAMPLES must be >= 200")
        if not (0.30 <= self.predictor_min_directional_accuracy <= 1.0):
            raise ValueError("Invalid directional accuracy threshold")
        if not (0 < self.predictor_max_brier <= 1):
            raise ValueError("Invalid Brier threshold")
        if not (0 < self.bearish_veto_probability <= 1):
            raise ValueError("Invalid bearish veto probability")
        if self.api_min_request_interval_seconds < 0:
            raise ValueError("API_MIN_REQUEST_INTERVAL_SECONDS must be nonneg")

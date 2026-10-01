"""
Main Bot Loop
Set PAPER_MODE = True  → uses real live prices but fake local balance (no real trades)
Set PAPER_MODE = False → live trading on your real Roostoo account
"""

import logging
import time
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

os.makedirs("logs", exist_ok=True)

# Mirror all print() output to both terminal and bot.log
class TeeStream:
    """Write to both terminal and log file simultaneously."""
    def __init__(self, file_path: str):
        self._terminal = sys.stdout
        self._file = open(file_path, "a", buffering=1)  # line-buffered

    def write(self, message):
        self._terminal.write(message)
        self._file.write(message)

    def flush(self):
        self._terminal.flush()
        self._file.flush()

    def isatty(self):
        return self._terminal.isatty()

sys.stdout = TeeStream("logs/bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

from src.api.client import RoostooClient
from src.paper_trader import PaperTrader
from src.strategy.momentum import MomentumStrategy, MomentumConfig
from src.risk.manager import RiskManager
from src.logger.trade_logger import TradeLogger
from src.db import BotDB

# ═══════════════════════════════════════════════════════
#  CONFIGURATION — edit these before running
# ═══════════════════════════════════════════════════════
PAPER_MODE         = True     # True = fake trades | False = real trades
STARTING_BALANCE   = 50_000.0 # paper balance (ignored in live mode)

POLL_INTERVAL      = 300      # 5 minutes — reduces noise and commission churn
PERF_INTERVAL      = 1800     # performance summary every 30 min

PROFIT_TARGET_PCT  = None     # disabled — run until Ctrl+C
STOP_LOSS_PCT      = None     # disabled — run until Ctrl+C
# ═══════════════════════════════════════════════════════

TRADE_PAIRS: list[str] = []


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def _ts_ms() -> int:
    return int(time.time() * 1000)


def get_exchange_pairs(client: RoostooClient) -> list[str]:
    info = client.exchange_info()
    return [p for p, m in info.get("TradePairs", {}).items() if m.get("CanTrade", False)]


def get_amount_precision(exchange_info: dict, pair: str) -> int:
    return exchange_info.get("TradePairs", {}).get(pair, {}).get("AmountPrecision", 6)


def get_min_order(exchange_info: dict, pair: str) -> float:
    return exchange_info.get("TradePairs", {}).get(pair, {}).get("MiniOrder", 1.0)


# ── Display helpers ───────────────────────────────────────────────────────────

def print_header(cycle: int, portfolio: float, usd_free: float, warmed: int, total: int,
                 paper: bool, avg_ticks: float = 0, ticks_needed: int = 22):
    pnl = portfolio - STARTING_BALANCE
    pnl_pct = (pnl / STARTING_BALANCE) * 100
    mode = "📄 PAPER" if paper else "💰 LIVE"
    icon = "📈" if pnl >= 0 else "📉"
    print(f"\n{'═'*62}")
    print(f"  🤖  Trade Bot [{mode}]  |  {now()}  |  Cycle #{cycle}")
    print(f"{'═'*62}")
    print(f"  💼  Portfolio:  ${portfolio:>12,.2f}   {icon} {pnl:+.2f} ({pnl_pct:+.4f}%)")
    print(f"  💵  USD Free:   ${usd_free:>12,.2f}")
    if warmed < total:
        pct = (avg_ticks / ticks_needed) * 100
        bar_filled = int(pct / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        print(f"  ⏳  Warmup:     [{bar}] {avg_ticks:.1f}/{ticks_needed} ticks  ({warmed}/{total} pairs ready)")
    else:
        print(f"  ✅  All {total} pairs warmed up — strategy active")
    print(f"{'─'*62}")


def print_signal(pair: str, price: float, signal: str, ind: dict, action: str = ""):
    icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(signal, "⚪")
    warming = ind.get("warming_up", False)
    if warming:
        print(f"  {icon}  {pair:<14}  ${price:>12,.4f}   warming up ({ind.get('prices_collected',0)}/25)")
    else:
        fast  = ind.get("fast_ema", 0)
        slow  = ind.get("slow_ema", 0)
        rsi   = ind.get("rsi", 0)
        trend = "▲" if fast > slow else "▼"
        line  = f"  {icon}  {pair:<14}  ${price:>12,.4f}   RSI={rsi:>5.1f}  EMA {trend}  {signal}"
        if action:
            line += f"  ← {action}"
        print(line)


def print_trade(result: dict, paper: bool):
    d = result.get("OrderDetail", result)
    side   = d.get("Side", "?")
    qty    = d.get("FilledQuantity", "?")
    pair   = d.get("Pair", "?")
    price  = d.get("FilledAverPrice") or d.get("Price", "?")
    status = d.get("Status", "?")
    oid    = d.get("OrderID", "?")
    comm   = d.get("CommissionChargeValue", 0)
    tag    = "[PAPER]" if paper else "[LIVE]"
    print(f"       ✅  {tag} {side} {qty} {pair} @ ${price}  fee=${comm}  ID={oid}  [{status}]")


def print_positions(paper_trader: PaperTrader, tickers: dict):
    positions = paper_trader.positions_summary(tickers)
    if not positions:
        return
    print(f"\n  {'Coin':<10} {'Qty':>14} {'Entry':>10} {'Now':>10} {'Value':>10} {'PnL':>10} {'%':>7}")
    print(f"  {'─'*76}")
    for p in positions:
        pnl_sym = "+" if p["unrealized_pnl"] >= 0 else ""
        print(
            f"  {p['coin']:<10} {p['qty']:>14,.4f} "
            f"${p['avg_entry']:>9,.4f} ${p['current_price']:>9,.4f} "
            f"${p['current_value']:>9,.2f} "
            f"{pnl_sym}${p['unrealized_pnl']:>8,.2f} "
            f"{p['unrealized_pct']:>+6.2f}%"
        )


def print_performance(summary: dict, portfolio: float):
    print(f"\n{'─'*62}")
    print(f"  📈  PERFORMANCE SUMMARY")
    print(f"{'─'*62}")
    print(f"  Total return:    {summary.get('total_return_pct', 0):+.4f}%")
    print(f"  Sharpe ratio:    {summary.get('sharpe_ratio', 0):.4f}")
    print(f"  Sortino ratio:   {summary.get('sortino_ratio', 0):.4f}")
    print(f"  Calmar ratio:    {summary.get('calmar_ratio', 0):.4f}")
    print(f"  Max drawdown:    {summary.get('max_drawdown_pct', 0):.4f}%")
    print(f"  Portfolio now:   ${portfolio:,.2f}")
    print(f"{'─'*62}")


def shutdown(reason: str, paper: PaperTrader | None, client: RoostooClient,
             trade_log: TradeLogger, tickers: dict,
             total_buys: int, total_sells: int):
    print(f"\n{'═'*62}")
    print(f"  🛑  {reason}")
    print(f"  {now()}")
    print(f"{'═'*62}")

    if PAPER_MODE and paper:
        # Close all paper positions
        positions = paper.positions_summary(tickers)
        if positions:
            print("\n  Closing all paper positions...")
            for p in positions:
                pair  = f"{p['coin']}/USD"
                qty   = p["qty"]
                price = p["current_price"]
                result = paper.place_order(pair, "SELL", qty, price=price)
                print_trade(result, paper=True)
                if result.get("Success"):
                    trade_log.log_order(result, note="shutdown_sell")
        final_val = paper.portfolio_value(tickers)
    else:
        # Close all real positions
        wallet = client.balance().get("SpotWallet") or {}
        tickers_now = client.get_all_tickers()
        for coin, amounts in wallet.items():
            if coin == "USD": continue
            qty = amounts.get("Free", 0.0)
            if qty <= 0: continue
            pair  = f"{coin}/USD"
            price = tickers_now.get(pair, {}).get("LastPrice", 0)
            print(f"  Selling {qty} {coin} @ ${price}")
            result = client.place_order(pair, "SELL", qty, order_type="MARKET")
            print_trade(result, paper=False)
            if result.get("Success"):
                trade_log.log_order(result, note="shutdown_sell")
        final_val = sum(
            (amounts.get("Free", 0) + amounts.get("Lock", 0)) *
            tickers_now.get(f"{c}/USD", {}).get("LastPrice", 0) if c != "USD" else
            amounts.get("Free", 0) + amounts.get("Lock", 0)
            for c, amounts in (client.balance().get("SpotWallet") or {}).items()
        )

    summary = trade_log.performance_summary(STARTING_BALANCE, final_val)
    print_performance(summary, final_val)
    print(f"  Total trades:  {total_buys} buys / {total_sells} sells")
    print(f"  Logs saved to: logs/")
    print(f"{'═'*62}\n")


def _restore_warmup(strategy: MomentumStrategy, db: "BotDB", pairs: list,
                    live_tickers: dict = None) -> int:
    """
    Load prices from DB into strategy for warmup.
    Uses _restoring flag to suppress stop-loss/take-profit during loading.
    Resets all position state after loading — clean slate for new session.
    Returns total number of price rows restored.
    """
    import time
    now_ms = int(time.time() * 1000)
    total  = 0

    strategy._restoring = True  # suppress stop-loss during restore

    for pair in pairs:
        rows = db.get_prices(pair, limit=200)
        if not rows:
            continue
        for row in reversed(rows):
            strategy.update(pair, row["price"])
            total += 1

        # Drift correction: if DB data > 10 min old, feed live price
        if live_tickers:
            live_price = live_tickers.get(pair, {}).get("LastPrice", 0)
            if live_price and rows:
                age_mins = (now_ms - rows[0]["timestamp_ms"]) / 60000
                if age_mins > 10:
                    strategy.update(pair, live_price)

        # Reset position tracking — clean slate, no open positions on start
        state = strategy._state(pair)
        state.entry_price  = 0.0
        state.hold_cycles  = 0
        state.last_signal  = "HOLD"
        state.just_restored = True  # block phantom crossover on first live tick

    strategy._restoring = False  # re-enable live stop-loss/take-profit

    # Mark all pairs as just-restored — skip signal on first live tick
    # to avoid false crossover from the last restore tick
    for pair in pairs:
        strategy._state(pair).last_signal = "HOLD"

    return total


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    mode_label = "📄 PAPER TRADING (no real orders)" if PAPER_MODE else "💰 LIVE TRADING"
    print(f"\n{'═'*62}")
    print(f"  🚀  ROOSTOO TRADE BOT — {mode_label}")
    print(f"  {now()}")
    print(f"{'═'*62}")

    client = RoostooClient()

    st = client.server_time()
    if "ServerTime" not in st:
        print("❌  Cannot reach API.")
        return
    print(f"  ✅  Connected  |  Server: {st['ServerTime']}")

    exchange_info = client.exchange_info()
    global TRADE_PAIRS
    TRADE_PAIRS = get_exchange_pairs(client)
    if not TRADE_PAIRS:
        print("❌  No tradable pairs.")
        return
    print(f"  ✅  {len(TRADE_PAIRS)} pairs loaded")

    # Init paper trader or live balance display
    paper = PaperTrader(starting_usd=STARTING_BALANCE) if PAPER_MODE else None

    if PAPER_MODE:
        print(f"  💵  Paper balance: ${STARTING_BALANCE:,.2f}")
    else:
        real_usd = client.get_usd_balance()
        print(f"  💵  Live USD balance: ${real_usd:,.2f}")

    if PROFIT_TARGET_PCT:
        print(f"  🎯  Profit target: +{PROFIT_TARGET_PCT}%  (${STARTING_BALANCE * (1 + PROFIT_TARGET_PCT/100):,.2f})")
    if STOP_LOSS_PCT:
        print(f"  🛡️   Stop loss:     -{STOP_LOSS_PCT}%  (${STARTING_BALANCE * (1 - STOP_LOSS_PCT/100):,.2f})")
    print(f"  🛑  Ctrl+C to stop\n")

    strategy  = MomentumStrategy(MomentumConfig(
        fast_ema=8,
        slow_ema=21,
        rsi_period=14,
        min_history=50,
        rsi_buy_min=40.0,      # only buy RSI 40–60 (healthy momentum zone)
        rsi_buy_max=60.0,
        rsi_sell_min=45.0,     # don't sell if RSI < 45 (could bounce)
        ema_separation_pct=0.02,  # crossover must be meaningful
        min_hold_cycles=3,     # hold at least 3 polls (15 min) before signal-sell
        min_profit_pct=0.25,   # must be +0.25% profit to sell on signal
        stop_loss_pct=2.0,     # hard stop at -2%
        take_profit_pct=3.0,   # take profit at +3%
    ))
    risk      = RiskManager(initial_balance=STARTING_BALANCE)
    trade_log = TradeLogger()
    db        = BotDB()
    db.set_state("mode", "paper" if PAPER_MODE else "live")
    db.set_state("start_balance", str(STARTING_BALANCE))
    db.set_state("started_at", str(_ts_ms()))
    logger.info("DB initialised — %s", db.stats())

    # ── Restore warmup from DB if enough history exists ───────────────
    print(f"  🔄  Fetching live prices for drift correction...")
    initial_tickers = client.get_all_tickers()
    restored = _restore_warmup(strategy, db, TRADE_PAIRS, live_tickers=initial_tickers)
    if restored:
        warmed_count = sum(1 for p in TRADE_PAIRS if not strategy.indicators(p).get("warming_up", False))
        print(f"  ♻️   Restored {restored:,} price rows from DB — {warmed_count}/{len(TRADE_PAIRS)} pairs already warmed up")
        if warmed_count == len(TRADE_PAIRS):
            print(f"  ✅  All pairs warmed — trading starts immediately!")
        else:
            remaining = len(TRADE_PAIRS) - warmed_count
            print(f"  ⏳  {remaining} pairs still need more ticks")
    else:
        print(f"  ⏱   No prior data — warmup needed (~{50*POLL_INTERVAL//3600}h {(50*POLL_INTERVAL%3600)//60}min)")

    last_perf   = time.time()
    cycle       = 0
    total_buys  = 0
    total_sells = 0

    while True:
        try:
            cycle += 1

            # ── Fetch live prices ─────────────────────────────────────
            all_tickers = client.get_all_tickers()
            if not all_tickers:
                print(f"  ⚠️  [{now()}] Empty ticker. Retrying in {POLL_INTERVAL}s...")
                db.log_api_event("/v3/ticker", False, "empty ticker response")
                time.sleep(POLL_INTERVAL)
                continue

            # Save all prices to DB
            ts_now = _ts_ms()
            db.insert_prices(all_tickers, timestamp_ms=ts_now)
            db.log_api_event("/v3/ticker", True, f"{len(all_tickers)} pairs")

            # ── Portfolio value ───────────────────────────────────────
            if PAPER_MODE:
                portfolio_val = paper.portfolio_value(all_tickers)
                usd_free      = paper.get_usd_balance()
            else:
                bal           = client.balance()
                wallet        = bal.get("SpotWallet") or {}
                usd_free      = wallet.get("USD", {}).get("Free", 0.0)
                portfolio_val = usd_free
                for coin, amounts in wallet.items():
                    if coin == "USD": continue
                    qty   = amounts.get("Free", 0) + amounts.get("Lock", 0)
                    price = all_tickers.get(f"{coin}/USD", {}).get("LastPrice", 0)
                    portfolio_val += qty * price

            risk.update_balance(portfolio_val)
            trade_log.snapshot(portfolio_val)
            db.insert_equity(portfolio_val, timestamp_ms=ts_now)

            # ── Auto-stop checks ──────────────────────────────────────
            pnl_pct = (portfolio_val - STARTING_BALANCE) / STARTING_BALANCE * 100
            if PROFIT_TARGET_PCT and pnl_pct >= PROFIT_TARGET_PCT:
                shutdown(f"🎯 PROFIT TARGET HIT: +{pnl_pct:.2f}%",
                         paper, client, trade_log, all_tickers, total_buys, total_sells)
                return
            if STOP_LOSS_PCT and pnl_pct <= -STOP_LOSS_PCT:
                shutdown(f"🔴 STOP LOSS HIT: {pnl_pct:.2f}%",
                         paper, client, trade_log, all_tickers, total_buys, total_sells)
                return

            # ── Header ───────────────────────────────────────────────
            warmed   = sum(1 for p in TRADE_PAIRS if not strategy.indicators(p).get("warming_up", False))
            # pairs that have at least 1 tick collected (warming up or done)
            have_data = sum(1 for p in TRADE_PAIRS if strategy.indicators(p).get("prices_collected", 0) > 0)
            total_needed = 50  # min_history
            # average ticks collected across all pairs
            avg_ticks = sum(
                strategy.indicators(p).get("prices_collected", 0) for p in TRADE_PAIRS
            ) / max(len(TRADE_PAIRS), 1)
            logger.info("Cycle %d: warmed=%d/%d avg_ticks=%.1f portfolio=%.2f usd_free=%.2f",
                        cycle, warmed, len(TRADE_PAIRS), avg_ticks, portfolio_val, usd_free)
            print_header(cycle, portfolio_val, usd_free, warmed, len(TRADE_PAIRS), PAPER_MODE, avg_ticks, total_needed)

            if risk.is_halted():
                print(f"  🛑  HALTED — drawdown {risk.drawdown(portfolio_val)*100:.2f}% exceeds limit.")
                time.sleep(POLL_INTERVAL)
                continue

            # ── Strategy loop ─────────────────────────────────────────
            actions = 0
            for pair in TRADE_PAIRS:
                ticker = all_tickers.get(pair)
                if not ticker:
                    continue
                price = ticker.get("LastPrice", 0.0)
                if not price:
                    continue

                signal = strategy.update(pair, price)
                ind    = strategy.indicators(pair)
                coin   = pair.split("/")[0]

                if not ind.get("warming_up", False):
                    print_signal(pair, price, signal, ind)
                if ind.get("warming_up", False):
                    continue

                # Get coin balance from the right source
                if PAPER_MODE:
                    coin_held = paper.get_coin_balance(coin)
                else:
                    coin_held = client.get_coin_balance(coin)

                amt_prec = get_amount_precision(exchange_info, pair)

                if signal == "BUY" and coin_held == 0:
                    # Guard: keep at least 20% as cash reserve
                    MIN_CASH_RESERVE = STARTING_BALANCE * 0.20
                    if usd_free < MIN_CASH_RESERVE:
                        logger.info("SKIP %s: usd_free=%.2f below reserve %.2f", pair, usd_free, MIN_CASH_RESERVE)
                        continue

                    size_usd = risk.position_size_usd(usd_free, portfolio_val)
                    logger.info("BUY candidate %s: size_usd=%.2f usd_free=%.2f", pair, size_usd, usd_free)
                    if size_usd <= 0:
                        logger.info("SKIP %s: size_usd <= 0", pair)
                        continue
                    factor = 10 ** amt_prec
                    qty = int((size_usd / price) * factor) / factor
                    if qty * price < get_min_order(exchange_info, pair):
                        logger.info("SKIP %s: order value %.2f < min_order %.2f", pair, qty*price, get_min_order(exchange_info, pair))
                        continue

                    print(f"\n  🟢  BUY → {pair}  qty={qty}  ~${qty*price:.2f}")
                    if PAPER_MODE:
                        result = paper.place_order(pair, "BUY", qty, price=price)
                    else:
                        result = client.place_order(pair, "BUY", qty, order_type="MARKET")

                    print_trade(result, PAPER_MODE)
                    if result.get("Success"):
                        trade_log.log_order(result, note="paper_buy" if PAPER_MODE else "live_buy")
                        db.insert_trade_from_order(result, mode="paper" if PAPER_MODE else "live")
                        filled_price = result.get("OrderDetail", {}).get("FilledAverPrice") or price
                        strategy.notify_bought(pair, filled_price)
                        usd_free -= size_usd
                        total_buys += 1
                        actions += 1

                elif signal == "SELL" and coin_held > 0:
                    ind = strategy.indicators(pair)
                    entry = ind.get("entry_price", 0)
                    pnl_pct = ((price - entry) / entry * 100) if entry > 0 else 0
                    reason = "STOP-LOSS" if pnl_pct <= -2.0 else "TAKE-PROFIT" if pnl_pct >= 3.0 else "SIGNAL"
                    print(f"\n  🔴  SELL [{reason}] → {pair}  qty={coin_held}  pnl={pnl_pct:+.2f}%  ~${coin_held*price:.2f}")
                    if PAPER_MODE:
                        result = paper.place_order(pair, "SELL", coin_held, price=price)
                    else:
                        result = client.place_order(pair, "SELL", coin_held, order_type="MARKET")

                    print_trade(result, PAPER_MODE)
                    if result.get("Success"):
                        trade_log.log_order(result, note="paper_sell" if PAPER_MODE else "live_sell")
                        db.insert_trade_from_order(result, mode="paper" if PAPER_MODE else "live")
                        strategy.notify_sold(pair)
                        total_sells += 1
                        actions += 1

            if actions == 0 and warmed == len(TRADE_PAIRS):
                print(f"  ⚪  No signals this cycle.")

            # Show paper positions table
            if PAPER_MODE and paper:
                print_positions(paper, all_tickers)

            print(f"\n  📦  Trades: {total_buys} buys / {total_sells} sells")
            print(f"  ⏳  Next poll in {POLL_INTERVAL}s...")

            # ── Periodic performance ──────────────────────────────────
            if time.time() - last_perf >= PERF_INTERVAL:
                summary = trade_log.performance_summary(STARTING_BALANCE, portfolio_val)
                print_performance(summary, portfolio_val)
                stats = db.stats()
                print(f"  🗄️   DB: {stats['price_rows']:,} price rows | {stats['trade_rows']} trades | {stats['equity_rows']} equity points")
                last_perf = time.time()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            shutdown("Stopped by user (Ctrl+C)",
                     paper, client, trade_log, all_tickers if 'all_tickers' in dir() else {},
                     total_buys, total_sells)
            db.set_state("stopped_at", str(_ts_ms()))
            print(f"  🗄️   DB saved: {db.stats()}")
            break

        except Exception as e:
            print(f"  ⚠️  [{now()}] Error: {e} — retrying in {POLL_INTERVAL}s")
            logger.exception("Unexpected error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()

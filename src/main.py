"""
Main Bot Loop
Set PAPER_MODE = True  -> uses real live prices but fake local balance (no real trades)
Set PAPER_MODE = False -> live trading on your real Roostoo account
"""

import logging
import time
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

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

class _ISTFormatter(logging.Formatter):
    """Logging formatter that stamps records in IST."""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=IST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S IST")

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_ISTFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("main")

from src.api.client import RoostooClient
from src.paper_trader import PaperTrader
from src.strategy.momentum import MomentumStrategy, MomentumConfig
from src.risk.manager import RiskManager
from src.logger.trade_logger import TradeLogger
from src.db import BotDB

# ===============================================================
#  CONFIGURATION -- edit these before running
# ===============================================================
PAPER_MODE         = False     # True = fake trades | False = real trades
STARTING_BALANCE   = 49_891.04 # starting balance -- used for risk sizing in both modes
                               # in live mode this is auto-set from real account on startup

POLL_INTERVAL      = 300      # 5 minutes -- reduces noise and commission churn
PERF_INTERVAL      = 900     # performance summary every 30 min

PROFIT_TARGET_PCT  = 1.7     # disabled -- run until Ctrl+C
STOP_LOSS_PCT      = 4.57      # stop if portfolio drops to ~$46,500
MAX_POSITIONS      = 6       # max open positions at once
# ===============================================================

TRADE_PAIRS: list[str] = []


def now() -> str:
    return datetime.now(IST).strftime("%H:%M:%S IST")

def _ts_ms() -> int:
    return int(time.time() * 1000)


def get_exchange_pairs(client: RoostooClient) -> list[str]:
    info = client.exchange_info()
    return [p for p, m in info.get("TradePairs", {}).items() if m.get("CanTrade", False)]


def get_amount_precision(exchange_info: dict, pair: str) -> int:
    return exchange_info.get("TradePairs", {}).get(pair, {}).get("AmountPrecision", 6)


def get_min_order(exchange_info: dict, pair: str) -> float:
    return exchange_info.get("TradePairs", {}).get(pair, {}).get("MiniOrder", 1.0)


# -- Display helpers -----------------------------------------------------------

def print_header(cycle: int, portfolio: float, usd_free: float, warmed: int, total: int,
                 paper: bool, avg_ticks: float = 0, ticks_needed: int = 22):
    pnl = portfolio - STARTING_BALANCE
    pnl_pct = (pnl / STARTING_BALANCE) * 100
    mode = "PAPER" if paper else "LIVE"
    icon = "Perf: UP" if pnl >= 0 else "Perf: DOWN"
    print(f"\n{'='*62}")
    print(f"  Bot  Trade Bot [{mode}]  |  {now()}  |  Cycle #{cycle}")
    print(f"{'='*62}")
    print(f"  Portfolio:  ${portfolio:>12,.2f}   {icon} {pnl:+.2f} ({pnl_pct:+.4f}%)")
    print(f"  USD:   ${usd_free:>12,.2f}")
    if warmed < total:
        pct = (avg_ticks / ticks_needed) * 100
        bar_filled = int(pct / 5)
        bar = "#" * bar_filled + "." * (20 - bar_filled)
        print(f"  Waiting  Warmup:     [{bar}] {avg_ticks:.1f}/{ticks_needed} ticks  ({warmed}/{total} pairs ready)")
    else:
        print(f"  [OK]  All {total} pairs warmed up -- strategy active")
    print(f"{'-'*62}")


def print_signal(pair: str, price: float, signal: str, ind: dict, action: str = ""):
    icon = {"BUY": "[BUY]", "SELL": "[SELL]", "HOLD": "[ ]"}.get(signal, "[ ]")
    warming = ind.get("warming_up", False)
    if warming:
        print(f"  {icon}  {pair:<14}  ${price:>12,.4f}   warming up ({ind.get('prices_collected',0)}/25)")
    else:
        fast  = ind.get("fast_ema", 0)
        slow  = ind.get("slow_ema", 0)
        rsi   = ind.get("rsi", 0)
        trend = "^" if fast > slow else "v"
        line  = f"  {icon}  {pair:<14}  ${price:>12,.4f}   RSI={rsi:>5.1f}  EMA {trend}  {signal}"
        if action:
            line += f"  <- {action}"
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
    print(f"       [OK]  {tag} {side} {qty} {pair} @ ${price}  fee=${comm}  ID={oid}  [{status}]")


def print_positions(paper_trader: PaperTrader, tickers: dict):
    positions = paper_trader.positions_summary(tickers)
    if not positions:
        return
    print(f"\n  {'Coin':<10} {'Qty':>14} {'Entry':>10} {'Now':>10} {'Value':>10} {'PnL':>10} {'%':>7}")
    print(f"  {'-'*76}")
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
    print(f"\n{'-'*62}")
    print(f"  Perf:  PERFORMANCE SUMMARY")
    print(f"{'-'*62}")
    print(f"  Total return:    {summary.get('total_return_pct', 0):+.4f}%")
    print(f"  Sharpe ratio:    {summary.get('sharpe_ratio', 0):.4f}")
    print(f"  Sortino ratio:   {summary.get('sortino_ratio', 0):.4f}")
    print(f"  Calmar ratio:    {summary.get('calmar_ratio', 0):.4f}")
    print(f"  Max drawdown:    {summary.get('max_drawdown_pct', 0):.4f}%")
    print(f"  Portfolio now:   ${portfolio:,.2f}")
    print(f"{'-'*62}")


def shutdown(reason: str, paper: PaperTrader | None, client: RoostooClient,
             trade_log: TradeLogger, tickers: dict,
             total_buys: int, total_sells: int, db=None):
    print(f"\n{'='*62}")
    print(f"  STOP  {reason}")
    print(f"  {now()}")
    print(f"{'='*62}")

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
        # Close all real positions -- use ONE wallet snapshot for sell loop
        # AND final_val so there is no race between two separate balance calls.
        wallet = client.balance().get("SpotWallet") or {}
        tickers_now = client.get_all_tickers()
        for coin, amounts in wallet.items():
            if coin == "USD": continue
            # Sell Free qty; locked qty may be in a pending order and can't be market-sold
            qty = amounts.get("Free", 0.0)
            if qty <= 0: continue
            pair  = f"{coin}/USD"
            price = tickers_now.get(pair, {}).get("LastPrice", 0)
            print(f"  Selling {qty} {coin} @ ${price}")
            result = client.place_order(pair, "SELL", qty, order_type="MARKET")
            print_trade(result, paper=False)
            if result.get("Success"):
                trade_log.log_order(result, note="shutdown_sell")
                if db is not None:
                    db.insert_trade_from_order(result, mode="live")
        # Reuse the SAME wallet snapshot; no second API call needed here
        final_val = sum(
            (amounts.get("Free", 0) + amounts.get("Lock", 0)) *
            tickers_now.get(f"{c}/USD", {}).get("LastPrice", 0) if c != "USD" else
            amounts.get("Free", 0) + amounts.get("Lock", 0)
            for c, amounts in wallet.items()
        )

    summary = trade_log.performance_summary(STARTING_BALANCE, final_val)
    print_performance(summary, final_val)
    print(f"  Total trades:  {total_buys} buys / {total_sells} sells")
    print(f"  Logs saved to: logs/")
    print(f"{'='*62}\n")


def _restore_warmup(strategy: MomentumStrategy, db: "BotDB", pairs: list,
                    live_tickers: dict = None) -> int:
    """
    Load prices from DB into strategy for warmup.
    Uses _restoring flag to suppress stop-loss/take-profit during loading.
    Resets all position state after loading -- clean slate for new session.
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

        # Reset position tracking -- clean slate, no open positions on start
        state = strategy._state(pair)
        state.entry_price  = 0.0
        state.hold_cycles  = 0
        state.last_signal  = "HOLD"
        state.just_restored = True  # block phantom crossover on first live tick

    strategy._restoring = False  # re-enable live stop-loss/take-profit

    # Mark all pairs as just-restored -- skip signal on first live tick
    # to avoid false crossover from the last restore tick
    for pair in pairs:
        strategy._state(pair).last_signal = "HOLD"

    return total


# -- Main loop -----------------------------------------------------------------

def run():
    global STARTING_BALANCE, TRADE_PAIRS
    mode_label = "PAPER TRADING (no real orders)" if PAPER_MODE else "LIVE TRADING"
    print(f"\n{'='*62}")
    print(f"  Starting  ROOSTOO TRADE BOT -- {mode_label}")
    print(f"  {now()}")
    print(f"{'='*62}")

    client = RoostooClient()

    st = client.server_time()
    if "ServerTime" not in st:
        print("[FAIL]  Cannot reach API.")
        return
    print(f"  [OK]  Connected  |  Server: {st['ServerTime']}")

    exchange_info = client.exchange_info()
    TRADE_PAIRS = get_exchange_pairs(client)
    if not TRADE_PAIRS:
        print("[FAIL]  No tradable pairs.")
        return
    print(f"  [OK]  {len(TRADE_PAIRS)} pairs loaded")

    # Init paper trader or live balance display
    paper = PaperTrader(starting_usd=STARTING_BALANCE) if PAPER_MODE else None

    if PAPER_MODE:
        print(f"  USD:  Paper balance: ${STARTING_BALANCE:,.2f}")
    else:
        # Auto-set STARTING_BALANCE from real account value so risk sizing is accurate
        real_bal     = client.balance().get("SpotWallet") or {}
        real_tickers = client.get_all_tickers()
        real_usd     = real_bal.get("USD", {}).get("Free", 0.0)
        real_coins   = sum(
            (real_bal[c].get("Free", 0) + real_bal[c].get("Lock", 0)) *
            real_tickers.get(f"{c}/USD", {}).get("LastPrice", 0)
            for c in real_bal if c != "USD"
        )
        real_total       = real_usd + real_coins
        STARTING_BALANCE = round(real_total, 2)
        print(f"  USD:  Live balance: ${real_usd:,.2f}  |  Total portfolio: ${real_total:,.2f}")
        print(f"  STARTING_BALANCE auto-set to ${STARTING_BALANCE:,.2f}")

    if PROFIT_TARGET_PCT:
        print(f"  Target:  Profit target: +{PROFIT_TARGET_PCT}%  (${STARTING_BALANCE * (1 + PROFIT_TARGET_PCT/100):,.2f})")
    if STOP_LOSS_PCT:
        print(f"  Guard:   Stop loss:     -{STOP_LOSS_PCT}%  (${STARTING_BALANCE * (1 - STOP_LOSS_PCT/100):,.2f})")
    print(f"  STOP  Ctrl+C to stop\n")

    strategy  = MomentumStrategy(MomentumConfig(
        fast_ema=8,
        slow_ema=21,
        rsi_period=14,
        min_history=50,
        rsi_buy_min=45.0,          # tighter -- avoid weak momentum (was 40)
        rsi_buy_max=58.0,          # tighter -- avoid overbought (was 60)
        rsi_sell_min=45.0,
        ema_separation_pct=0.05,   # stronger crossover required (was 0.02)
        confirm_ticks=2,           # must be above slow EMA for 2 ticks before BUY
        min_hold_cycles=3,
        min_profit_pct=0.25,
        stop_loss_pct=2.0,
        take_profit_pct=3.0,
        loss_cooldown_cycles=3,    # 3-cycle lockout after stop-loss
    ))
    risk      = RiskManager(initial_balance=STARTING_BALANCE)
    trade_log = TradeLogger()
    db        = BotDB()
    db.set_state("mode", "paper" if PAPER_MODE else "live")
    db.set_state("start_balance", str(STARTING_BALANCE))
    db.set_state("started_at", str(_ts_ms()))
    logger.info("DB initialised -- %s", db.stats())

    # -- Restore warmup from DB if enough history exists ---------------
    print(f"  Loading  Fetching live prices for drift correction...")
    initial_tickers = client.get_all_tickers()
    restored = _restore_warmup(strategy, db, TRADE_PAIRS, live_tickers=initial_tickers)
    if restored:
        warmed_count = sum(1 for p in TRADE_PAIRS if not strategy.indicators(p).get("warming_up", False))
        print(f"  Restored  Restored {restored:,} price rows from DB -- {warmed_count}/{len(TRADE_PAIRS)} pairs already warmed up")
        if warmed_count == len(TRADE_PAIRS):
            print(f"  [OK]  All pairs warmed -- trading starts immediately!")
        else:
            remaining = len(TRADE_PAIRS) - warmed_count
            print(f"  Waiting  {remaining} pairs still need more ticks")
    else:
        print(f"  No prior data -- warmup needed (~{50*POLL_INTERVAL//3600}h {(50*POLL_INTERVAL%3600)//60}min)")

    # -- Reconcile existing live/paper positions into strategy state ---
    # If the account already holds coins (e.g. UNI from manual trade),
    # register them so the strategy tracks stop-loss and take-profit correctly.
    if not PAPER_MODE:
        existing_wallet = client.balance().get("SpotWallet") or {}
        reconciled = []
        for coin, amounts in existing_wallet.items():
            if coin == "USD":
                continue
            qty = amounts.get("Free", 0) + amounts.get("Lock", 0)
            if qty <= 0:
                continue
            pair  = f"{coin}/USD"
            price = initial_tickers.get(pair, {}).get("LastPrice", 0)
            if price and pair in TRADE_PAIRS:
                strategy.notify_bought(pair, price)
                reconciled.append(f"{pair}@${price:.4f}")
        if reconciled:
            print(f"  Reconciled {len(reconciled)} existing positions: {', '.join(reconciled)}")
            print(f"  Stop-loss and take-profit now active on these holdings.")

    last_perf   = time.time()
    cycle       = 0
    total_buys  = 0
    total_sells = 0

    while True:
        try:
            cycle += 1

            # -- Fetch live prices -------------------------------------
            all_tickers = client.get_all_tickers()
            if not all_tickers:
                print(f"  [WARN]  [{now()}] Empty ticker. Retrying in {POLL_INTERVAL}s...")
                db.log_api_event("/v3/ticker", False, "empty ticker response")
                time.sleep(POLL_INTERVAL)
                continue

            # Save all prices to DB
            ts_now = _ts_ms()
            db.insert_prices(all_tickers, timestamp_ms=ts_now)
            db.log_api_event("/v3/ticker", True, f"{len(all_tickers)} pairs")

            # -- Portfolio value ---------------------------------------
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

            # -- Auto-stop checks --------------------------------------
            pnl_pct = (portfolio_val - STARTING_BALANCE) / STARTING_BALANCE * 100
            if PROFIT_TARGET_PCT and pnl_pct >= PROFIT_TARGET_PCT:
                shutdown(f"Target: PROFIT TARGET HIT: +{pnl_pct:.2f}%",
                         paper, client, trade_log, all_tickers, total_buys, total_sells, db)
                return
            if STOP_LOSS_PCT and pnl_pct <= -STOP_LOSS_PCT:
                shutdown(f"[SELL] STOP LOSS HIT: {pnl_pct:.2f}%",
                         paper, client, trade_log, all_tickers, total_buys, total_sells, db)
                return

            # -- Header -----------------------------------------------
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
                print(f"  STOP  HALTED -- drawdown {risk.drawdown(portfolio_val)*100:.2f}% exceeds limit.")
                time.sleep(POLL_INTERVAL)
                continue

            # -- Strategy loop -----------------------------------------
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
                    # BUG FIX: use Free + Lock so a locked (pending-order) balance
                    # still shows up and we don't skip the SELL signal.
                    live_wallet = client.balance().get("SpotWallet") or {}
                    coin_amounts = live_wallet.get(coin.upper(), {})
                    coin_held = coin_amounts.get("Free", 0.0) + coin_amounts.get("Lock", 0.0)

                amt_prec = get_amount_precision(exchange_info, pair)

                # -- Trailing stop: once up >1.5%, move stop to breakeven --
                if coin_held > 0:
                    held_ind   = strategy.indicators(pair)
                    held_entry = held_ind.get("entry_price", 0)
                    if held_entry > 0:
                        current_pnl_pct = (price - held_entry) / held_entry * 100
                        # If up >1.5%, update entry_price to breakeven (entry + fees)
                        # This means the stop-loss now protects against any loss
                        breakeven = held_entry * 1.002  # entry + 0.2% round-trip commission
                        state = strategy._state(pair)
                        if current_pnl_pct >= 1.5 and state.entry_price < breakeven:
                            old_stop = state.entry_price * 0.98
                            state.entry_price = breakeven
                            new_stop = state.entry_price * 0.98
                            logger.info("TRAILING STOP %s: pnl=%.2f%% -> entry moved from %.4f to %.4f (stop %.4f -> %.4f)",
                                        pair, current_pnl_pct, held_entry, breakeven, old_stop, new_stop)

                # -- Proactive rotation: even when slots are free, --
                # sell a position that is consistently losing ground  --
                # IF a stronger signal exists on another pair.
                if coin_held > 0 and signal == "HOLD":
                    held_ind   = strategy.indicators(pair)
                    held_entry = held_ind.get("entry_price", 0)
                    if held_entry > 0:
                        held_pnl_pct = (price - held_entry) / held_entry * 100
                        held_cycles  = held_ind.get("hold_cycles", 0)
                        fast = held_ind.get("fast_ema", 0)
                        slow = held_ind.get("slow_ema", 0)
                        rsi  = held_ind.get("rsi", 50)
                        # Proactively exit if: losing >0.8%, EMA turned down,
                        # RSI < 40 (weakening), held for at least 3 cycles
                        if (held_pnl_pct <= -0.8
                                and fast < slow          # trend reversed
                                and rsi < 40             # momentum weakening
                                and held_cycles >= 3     # not a new position
                                and held_pnl_pct > -2.0  # stop-loss hasn't caught it yet
                        ):
                            if PAPER_MODE:
                                sell_qty_proactive = coin_held
                            else:
                                sell_qty_proactive = live_wallet.get(coin.upper(), {}).get("Free", 0)
                            if sell_qty_proactive > 0:
                                print(f"\n  [SELL]  PROACTIVE EXIT -> {pair}  pnl={held_pnl_pct:+.2f}%  EMA down  RSI={rsi:.0f}")
                                if PAPER_MODE:
                                    p_result = paper.place_order(pair, "SELL", sell_qty_proactive, price=price)
                                else:
                                    p_result = client.place_order(pair, "SELL", sell_qty_proactive, order_type="MARKET")
                                print_trade(p_result, PAPER_MODE)
                                if p_result.get("Success"):
                                    trade_log.log_order(p_result, note="proactive_exit")
                                    db.insert_trade_from_order(p_result, mode="paper" if PAPER_MODE else "live")
                                    strategy.notify_sold(pair, was_loss=True)
                                    if PAPER_MODE:
                                        usd_free = paper.get_usd_balance()
                                    else:
                                        _pw = client.balance().get("SpotWallet") or {}
                                        usd_free = _pw.get("USD", {}).get("Free", 0.0)
                                    total_sells += 1
                                    actions += 1
                                    continue  # skip to next pair

                amt_prec = get_amount_precision(exchange_info, pair)

                if signal == "BUY" and coin_held == 0:
                    # Count open positions from actual in-memory state
                    if PAPER_MODE:
                        open_coins = {
                            coin: paper.get_coin_balance(coin)
                            for pair_ in TRADE_PAIRS
                            for coin in [pair_.split("/")[0]]
                            if paper.get_coin_balance(coin) > 0
                        }
                    else:
                        bal_wallet = client.balance().get("SpotWallet") or {}
                        # BUG FIX (BUG 4): count positions using Free+Lock so coins
                        # sitting in pending orders aren't invisible to the position cap.
                        # Store (free, total) tuples so we can sell only the Free qty.
                        open_coins = {
                            c: (a.get("Free", 0), a.get("Free", 0) + a.get("Lock", 0))
                            for c, a in bal_wallet.items()
                            if c != "USD" and (a.get("Free", 0) + a.get("Lock", 0)) > 0
                        }
                    open_positions = len(open_coins)

                    # -- Rotation: if full, check if we should swap a loser --
                    if open_positions >= MAX_POSITIONS:
                        # Score new signal strength: RSI closeness to 52 midpoint + sep
                        new_ind = strategy.indicators(pair)
                        new_rsi = new_ind.get("rsi", 50)
                        new_sep = new_ind.get("ema_sep_pct", 0)
                        new_score = new_sep * 10 + (1 - abs(new_rsi - 52) / 10)

                        # Find worst current position
                        worst_pair = None
                        worst_pnl  = 0.0  # only consider negatives

                        for held_coin, held_qty_info in open_coins.items():
                            # paper: held_qty_info is a plain float
                            # live:  held_qty_info is (free, total) tuple
                            held_qty_total = held_qty_info[1] if isinstance(held_qty_info, tuple) else held_qty_info
                            held_pair  = f"{held_coin}/USD"
                            held_price = all_tickers.get(held_pair, {}).get("LastPrice", 0)
                            held_ind   = strategy.indicators(held_pair)
                            held_entry = held_ind.get("entry_price", 0)
                            if held_entry <= 0 or held_price <= 0:
                                continue
                            held_pnl_pct = (held_price - held_entry) / held_entry * 100

                            # Only rotate out if:
                            # - Position is losing (negative PnL)
                            # - Loss is between -0.5% and -1.8% (below -2% = stop-loss handles it)
                            # - New signal is meaningfully stronger
                            if -1.8 <= held_pnl_pct <= -0.5 and held_pnl_pct < worst_pnl:
                                worst_pnl  = held_pnl_pct
                                worst_pair = held_pair

                        if worst_pair and new_score > 0.5:
                            worst_coin = worst_pair.split("/")[0]
                            # BUG FIX (BUG 4): sell only Free (settleable) qty.
                            # Locked qty is in a pending order and can't be market-sold.
                            worst_qty_info  = open_coins[worst_coin]
                            if isinstance(worst_qty_info, tuple):
                                worst_qty_free  = worst_qty_info[0]  # sellable
                            else:
                                worst_qty_free  = worst_qty_info
                            worst_price = all_tickers.get(worst_pair, {}).get("LastPrice", 0)
                            print(f"\n  Loading  ROTATE: selling {worst_pair} ({worst_pnl:+.2f}%) to buy {pair}")
                            if PAPER_MODE:
                                rot_result = paper.place_order(worst_pair, "SELL", worst_qty_free, price=worst_price)
                            else:
                                rot_result = client.place_order(worst_pair, "SELL", worst_qty_free, order_type="MARKET")

                            if rot_result.get("Success"):
                                trade_log.log_order(rot_result, note="rotation_sell")
                                db.insert_trade_from_order(rot_result, mode="paper" if PAPER_MODE else "live")
                                strategy.notify_sold(worst_pair, was_loss=(worst_pnl < 0))
                                print_trade(rot_result, PAPER_MODE)
                                if PAPER_MODE:
                                    usd_free = paper.get_usd_balance()
                                else:
                                    # BUG FIX (BUG 5): re-fetch real USD balance after rotation
                                    # so the subsequent BUY uses the accurate available amount.
                                    _post_rot = client.balance().get("SpotWallet") or {}
                                    usd_free = _post_rot.get("USD", {}).get("Free", 0.0)
                                open_positions -= 1
                                total_sells += 1
                        else:
                            logger.info("SKIP %s: max positions %d/%d reached, no rotation candidate",
                                        pair, open_positions, MAX_POSITIONS)
                            continue

                    # Guard: keep at least 20% as cash reserve
                    MIN_CASH_RESERVE = STARTING_BALANCE * 0.20
                    if usd_free < MIN_CASH_RESERVE:
                        logger.info("SKIP %s: usd_free=%.2f below reserve %.2f", pair, usd_free, MIN_CASH_RESERVE)
                        continue

                    size_usd = risk.position_size_usd(usd_free, portfolio_val)
                    logger.info("BUY candidate %s: size_usd=%.2f usd_free=%.2f open=%d/%d",
                                pair, size_usd, usd_free, open_positions, MAX_POSITIONS)
                    if size_usd <= 0:
                        logger.info("SKIP %s: size_usd <= 0", pair)
                        continue
                    factor = 10 ** amt_prec
                    qty = int((size_usd / price) * factor) / factor
                    if qty * price < get_min_order(exchange_info, pair):
                        logger.info("SKIP %s: order value %.2f < min_order %.2f", pair, qty*price, get_min_order(exchange_info, pair))
                        continue

                    # Commission filter: skip if $7.50 fee > 0.15% of position value
                    # This blocks low-price meme coins (BONK, SHIB, FLOKI) where fee eats profit
                    TAKER_FEE_USD = qty * price * 0.001  # 0.1% taker fee
                    FEE_THRESHOLD = qty * price * 0.0015  # fee should be < 0.15% of position
                    if TAKER_FEE_USD > FEE_THRESHOLD:
                        logger.info("SKIP %s: fee $%.2f > 0.15%% of position $%.2f", pair, TAKER_FEE_USD, qty*price)
                        continue
                    # Also skip if position value < $1000 (commission drag too high)
                    if qty * price < 1000:
                        logger.info("SKIP %s: position value $%.2f too small for commission efficiency", pair, qty*price)
                        continue

                    print(f"\n  [BUY]  BUY -> {pair}  qty={qty}  ~${qty*price:.2f}  [{open_positions+1}/{MAX_POSITIONS}]")
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
                        if PAPER_MODE:
                            usd_free = paper.get_usd_balance()
                        else:
                            # BUG FIX (BUG 3): re-fetch real USD balance after every BUY
                            # so that a second BUY in the same cycle uses the true
                            # remaining balance, not an approximate local decrement.
                            _post_buy = client.balance().get("SpotWallet") or {}
                            usd_free = _post_buy.get("USD", {}).get("Free", 0.0)
                        total_buys += 1
                        actions += 1

                elif signal == "SELL" and coin_held > 0:
                    ind     = strategy.indicators(pair)
                    entry   = ind.get("entry_price", 0)
                    pnl_pct = ((price - entry) / entry * 100) if entry > 0 else 0
                    was_loss = pnl_pct <= -2.0
                    reason  = "STOP-LOSS" if was_loss else "TAKE-PROFIT" if pnl_pct >= 3.0 else "SIGNAL"
                    # BUG FIX (BUG 2): sell only the Free (settleable) qty.
                    # coin_held is Free+Lock (used for position detection above), but
                    # locked qty is in a pending order and can't be market-sold.
                    if PAPER_MODE:
                        sell_qty = coin_held  # paper has no Lock
                    else:
                        sell_qty = coin_amounts.get("Free", 0.0)
                        if sell_qty <= 0:
                            logger.warning("SKIP SELL %s: coin_held=%.6f but Free=0 (all locked)", pair, coin_held)
                            continue
                    print(f"\n  [SELL]  SELL [{reason}] -> {pair}  qty={sell_qty}  pnl={pnl_pct:+.2f}%  ~${sell_qty*price:.2f}")
                    if PAPER_MODE:
                        result = paper.place_order(pair, "SELL", sell_qty, price=price)
                    else:
                        result = client.place_order(pair, "SELL", sell_qty, order_type="MARKET")

                    print_trade(result, PAPER_MODE)
                    if result.get("Success"):
                        trade_log.log_order(result, note="paper_sell" if PAPER_MODE else "live_sell")
                        db.insert_trade_from_order(result, mode="paper" if PAPER_MODE else "live")
                        strategy.notify_sold(pair, was_loss=was_loss)
                        total_sells += 1
                        actions += 1

            if actions == 0 and warmed == len(TRADE_PAIRS):
                print(f"  [ ]  No signals this cycle.")

            # Show paper positions table
            if PAPER_MODE and paper:
                print_positions(paper, all_tickers)

            print(f"\n  Trades: {total_buys} buys / {total_sells} sells")
            print(f"  Waiting  Next poll in {POLL_INTERVAL}s...")

            # -- Periodic performance ----------------------------------
            if time.time() - last_perf >= PERF_INTERVAL:
                summary = trade_log.performance_summary(STARTING_BALANCE, portfolio_val)
                print_performance(summary, portfolio_val)
                stats = db.stats()
                print(f"  DB: {stats['price_rows']:,} price rows | {stats['trade_rows']} trades | {stats['equity_rows']} equity points")
                last_perf = time.time()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            shutdown("Stopped by user (Ctrl+C)",
                     paper, client, trade_log, all_tickers if 'all_tickers' in dir() else {},
                     total_buys, total_sells, db)
            db.set_state("stopped_at", str(_ts_ms()))
            print(f"  DB saved: {db.stats()}")
            break

        except Exception as e:
            print(f"  [WARN]  [{now()}] Error: {e} -- retrying in {POLL_INTERVAL}s")
            logger.exception("Unexpected error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()

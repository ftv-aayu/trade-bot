import argparse
from .main import run

parser = argparse.ArgumentParser(prog="trade-bot", description="Crypto & stock prediction bot")
parser.add_argument(
    "--mode",
    choices=["crypto", "stock", "both"],
    default="both",
    help="Which universe to run: crypto (Binance), stock (Yahoo Finance), or both (default)",
)
args = parser.parse_args()
run(mode=args.mode)

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class TradeLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.performance_path = self.log_dir / "performance.jsonl"
        self._snapshots: list[float] = []

    def snapshot(self, label: str, extra: dict | None = None) -> None:
        row: dict = {"timestamp": datetime.now(IST).isoformat(), "label": label}
        if extra:
            row.update(extra)
        with self.performance_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    def log_prediction_summary(self, symbols: list[str], scores: dict[str, float],
                               predictions: dict, regime: str, model_enabled: bool) -> None:
        top = sorted(symbols, key=lambda s: scores.get(s, 0), reverse=True)[:10]
        self.snapshot("prediction_summary", {
            "regime": regime,
            "model_enabled": model_enabled,
            "top_symbols": top,
            "scores": {s: round(scores.get(s, 0), 4) for s in top},
        })

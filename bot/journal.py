"""Журнал: CSV, по строке на каждую проверку сигнала и на каждую сделку.

signals.csv — каждая закрытая 15m-свеча каждого символа со всеми факторами
(основа для последующей шлифовки порогов).
trades.csv — открытие и итог сделки (PnL, длительность, чем закрылась).
Время — UTC, ISO 8601.
"""
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from bot.signals import SignalCheck

log = logging.getLogger("bot.journal")

SIGNAL_FIELDS = [
    "ts", "symbol", "tf", "close", "vol_ratio",
    "macd", "macd_signal", "hist", "cross_dir", "cross_age", "hist_impulse",
    "trend_4h", "level_price", "level_kind", "level_dist_pct", "breakout",
    "direction", "reasons", "trade_opened", "skip_reason",
    "qty", "entry", "stop_loss", "take_profit",
]

TRADE_FIELDS = [
    "opened_ts", "closed_ts", "symbol", "side", "qty",
    "entry", "exit", "stop_loss", "take_profit",
    "pnl", "duration_min", "close_reason",
]


class Journal:
    def __init__(self, journal_dir: Path):
        journal_dir.mkdir(parents=True, exist_ok=True)
        self.signals_path = journal_dir / "signals.csv"
        self.trades_path = journal_dir / "trades.csv"
        self._ensure_header(self.signals_path, SIGNAL_FIELDS)
        self._ensure_header(self.trades_path, TRADE_FIELDS)

    @staticmethod
    def _ensure_header(path: Path, fields: list[str]) -> None:
        if not path.exists() or path.stat().st_size == 0:
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(fields)

    @staticmethod
    def _append(path: Path, fields: list[str], row: dict) -> None:
        with path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=fields).writerow(row)

    def log_check(self, s: SignalCheck, trade_opened: bool = False,
                  skip_reason: str = "", qty: float | None = None,
                  entry: float | None = None, sl: float | None = None,
                  tp: float | None = None) -> None:
        self._append(self.signals_path, SIGNAL_FIELDS, {
            "ts": s.ts.isoformat(), "symbol": s.symbol, "tf": s.tf,
            "close": s.close, "vol_ratio": round(s.vol_ratio, 4) if s.vol_ratio == s.vol_ratio else "",
            "macd": round(s.macd, 6), "macd_signal": round(s.macd_signal, 6),
            "hist": round(s.hist, 6),
            "cross_dir": s.cross_dir or "", "cross_age": s.cross_age if s.cross_age is not None else "",
            "hist_impulse": s.hist_impulse or "", "trend_4h": s.trend_4h,
            "level_price": s.level_price or "", "level_kind": s.level_kind or "",
            "level_dist_pct": round(s.level_dist_pct, 5) if s.level_dist_pct is not None else "",
            "breakout": s.breakout, "direction": s.direction or "",
            "reasons": "; ".join(s.reasons),
            "trade_opened": trade_opened, "skip_reason": skip_reason,
            "qty": qty or "", "entry": entry or "", "stop_loss": sl or "", "take_profit": tp or "",
        })

    def log_trade_closed(self, opened_ts: datetime | None, closed_ts: datetime,
                         symbol: str, side: str, qty: float,
                         entry: float, exit_price: float,
                         sl: float | None, tp: float | None,
                         pnl: float, close_reason: str) -> None:
        duration = ""
        if opened_ts is not None:
            duration = round((closed_ts - opened_ts).total_seconds() / 60, 1)
        self._append(self.trades_path, TRADE_FIELDS, {
            "opened_ts": opened_ts.isoformat() if opened_ts else "",
            "closed_ts": closed_ts.isoformat(),
            "symbol": symbol, "side": side, "qty": qty,
            "entry": entry, "exit": exit_price,
            "stop_loss": sl or "", "take_profit": tp or "",
            "pnl": pnl, "duration_min": duration, "close_reason": close_reason,
        })
        log.info("сделка закрыта: %s %s pnl=%.2f (%s)", symbol, side, pnl, close_reason)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

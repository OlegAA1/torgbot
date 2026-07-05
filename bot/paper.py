"""Виртуальный портфель для dry-run.

Симулирует открытие позиций по сигналам и закрытие по SL/TP на закрытых
свечах, чтобы dry-run проходил через те же риск-лимиты (кулдауны, лимит
позиций, потолок notional), что и боевая торговля — иначе журнал dry-run
несопоставим с боевым поведением. Позиции переживают перезапуск через
paper_positions.json.

Если свеча зацепила и SL и TP одновременно, засчитывается SL (консервативно).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from bot.risk import TradePlan

log = logging.getLogger("bot.paper")


class PaperBroker:
    def __init__(self, path: Path):
        self.path = path
        self.positions: dict[str, dict] = {}
        self._load()

    # ---------- персистентность ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.positions = json.loads(self.path.read_text())
            if self.positions:
                log.info("виртуальные позиции загружены: %s", ", ".join(self.positions))
        except Exception:
            log.exception("не удалось прочитать %s — начинаю с пустого портфеля", self.path.name)
            self.positions = {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.positions, indent=2))

    # ---------- операции ----------

    def open(self, plan: TradePlan, opened_at: datetime) -> None:
        self.positions[plan.symbol] = {
            "side": plan.side,
            "qty": plan.qty,
            "entry": plan.entry,
            "sl": plan.stop_loss,
            "tp": plan.take_profit,
            "opened_ts": opened_at.isoformat(),
        }
        self._save()
        log.info("[PAPER] открыта %s %s qty=%s entry=%s SL=%s TP=%s",
                 plan.side, plan.symbol, plan.qty, plan.entry, plan.stop_loss, plan.take_profit)

    def check_bar(self, symbol: str, high: float, low: float,
                  closed_at: datetime) -> dict | None:
        """Проверка SL/TP по high/low закрытой свечи. Возвращает итог закрытия или None."""
        p = self.positions.get(symbol)
        if p is None:
            return None
        if p["side"] == "Buy":
            hit_sl, hit_tp = low <= p["sl"], high >= p["tp"]
        else:
            hit_sl, hit_tp = high >= p["sl"], low <= p["tp"]
        if not (hit_sl or hit_tp):
            return None

        exit_price = p["sl"] if hit_sl else p["tp"]
        reason = "SL" if hit_sl else "TP"
        sign = 1 if p["side"] == "Buy" else -1
        pnl = sign * (exit_price - p["entry"]) * p["qty"]
        del self.positions[symbol]
        self._save()
        return {
            "symbol": symbol, "side": p["side"], "qty": p["qty"],
            "entry": p["entry"], "exit": exit_price,
            "sl": p["sl"], "tp": p["tp"], "pnl": pnl, "reason": reason,
            "opened_ts": datetime.fromisoformat(p["opened_ts"]),
            "closed_ts": closed_at.astimezone(timezone.utc),
        }

    def total_notional(self) -> float:
        return sum(p["qty"] * p["entry"] for p in self.positions.values())

"""Риск-менеджмент: размер позиции, SL/TP, лимиты, kill switch, кулдаун.

Жёсткие правила (не обходить):
  * риск на сделку = RISK_PER_TRADE от баланса;
  * не более MAX_POS_PER_SYMBOL позиций на символ и MAX_POS_TOTAL всего;
  * MAX_CONSEC_STOPS_PER_DAY стопов подряд -> запрет торговли до конца дня UTC;
  * кулдаун COOLDOWN_BARS свечей после закрытия сделки по символу;
  * никакого усреднения (вход запрещён при открытой позиции по символу);
  * стоп никогда не двигается против позиции (бот вообще не двигает стопы).

Состояние переживает перезапуск через state.json.
"""
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bot import config as cfg

log = logging.getLogger("bot.risk")


@dataclass
class TradePlan:
    symbol: str
    side: str        # 'Buy' | 'Sell'
    qty: float
    entry: float     # ориентировочная цена (Market-ордер)
    stop_loss: float
    take_profit: float
    risk_usdt: float


def build_plan(symbol: str, direction: str, price: float, level_price: float,
               balance: float, qty_step: float, min_qty: float,
               atr: float | None = None, open_notional: float = 0.0,
               tp_obstacle: float | None = None) -> tuple[TradePlan | None, str]:
    """Размер позиции = риск / расстояние до стопа. Возвращает (план, причина отказа).

    Стоп ближе max(MIN_SL_ATR_MULT*ATR, MIN_SL_PCT*цена) отодвигается до минимума
    (qty при этом пересчитывается). Notional капится: одна позиция <=
    MAX_POS_NOTIONAL_PCT баланса, все вместе <= MAX_TOTAL_NOTIONAL_PCT
    (open_notional — сумма qty*entry уже открытых позиций).

    Тейк: RISK_REWARD*R, но при USE_STRUCT_TP не дальше tp_obstacle (ближайший
    старый пивот по ходу сделки) минус буфер; если такой тейк ближе MIN_TP_RR*R —
    сделка не открывается (потенциал не окупает риск).
    """
    # сторона уровня: лонг только над уровнем, шорт только под ним
    # (сигнальная логика это уже гарантирует — здесь последний рубеж)
    if direction == "long" and price <= level_price:
        return None, f"цена {price:.4f} не выше уровня {level_price:.4f} — лонг отклонён"
    if direction == "short" and price >= level_price:
        return None, f"цена {price:.4f} не ниже уровня {level_price:.4f} — шорт отклонён"

    if direction == "long":
        sl = level_price * (1 - cfg.SL_BUFFER_PCT)
        if sl >= price:
            return None, f"стоп {sl:.4f} не ниже цены {price:.4f} — сигнал отклонён"
    else:
        sl = level_price * (1 + cfg.SL_BUFFER_PCT)
        if sl <= price:
            return None, f"стоп {sl:.4f} не выше цены {price:.4f} — сигнал отклонён"

    min_dist = cfg.MIN_SL_PCT * price
    if atr is not None and math.isfinite(atr) and atr > 0:
        min_dist = max(min_dist, cfg.MIN_SL_ATR_MULT * atr)
    if abs(price - sl) < min_dist:
        old_sl = sl
        sl = price - min_dist if direction == "long" else price + min_dist
        log.info("%s: стоп %.6f ближе минимума %.6f — отодвинут до %.6f",
                 symbol, old_sl, min_dist, sl)

    dist = abs(price - sl)
    if direction == "long":
        tp, side = price + cfg.RISK_REWARD * dist, "Buy"
    else:
        tp, side = price - cfg.RISK_REWARD * dist, "Sell"

    # структурный тейк: не целимся сквозь ближайшее препятствие
    if cfg.USE_STRUCT_TP and tp_obstacle is not None:
        if direction == "long":
            struct_tp = tp_obstacle * (1 - cfg.STRUCT_TP_BUFFER_PCT)
            if struct_tp < tp:
                if struct_tp - price < cfg.MIN_TP_RR * dist:
                    return None, (f"препятствие {tp_obstacle:.6f} слишком близко: "
                                  f"TP < {cfg.MIN_TP_RR}R — потенциал не окупает риск")
                log.info("%s: TP срезан препятствием %.6f: %.6f -> %.6f",
                         symbol, tp_obstacle, tp, struct_tp)
                tp = struct_tp
        else:
            struct_tp = tp_obstacle * (1 + cfg.STRUCT_TP_BUFFER_PCT)
            if struct_tp > tp:
                if price - struct_tp < cfg.MIN_TP_RR * dist:
                    return None, (f"препятствие {tp_obstacle:.6f} слишком близко: "
                                  f"TP < {cfg.MIN_TP_RR}R — потенциал не окупает риск")
                log.info("%s: TP срезан препятствием %.6f: %.6f -> %.6f",
                         symbol, tp_obstacle, tp, struct_tp)
                tp = struct_tp

    risk_usdt = balance * cfg.RISK_PER_TRADE
    qty = risk_usdt / dist

    cap = balance * cfg.MAX_POS_NOTIONAL_PCT
    total_room = balance * cfg.MAX_TOTAL_NOTIONAL_PCT - open_notional
    cap = min(cap, total_room)
    if cap <= 0:
        return None, (f"суммарный notional {open_notional:.0f} USDT исчерпал лимит "
                      f"{cfg.MAX_TOTAL_NOTIONAL_PCT:.0%} баланса")
    if qty * price > cap:
        qty = cap / price
        risk_usdt = qty * dist  # фактический риск после капа

    qty = math.floor(qty / qty_step) * qty_step
    qty = round(qty, 10)
    if qty < min_qty:
        return None, f"qty {qty} < minOrderQty {min_qty}"
    return TradePlan(symbol, side, qty, price, round(sl, 6), round(tp, 6), risk_usdt), ""


class RiskState:
    """Лимиты и kill switch. Открытые позиции сюда сообщает main по данным биржи."""

    def __init__(self, state_path: Path):
        self.path = state_path
        self.open_symbols: set[str] = set()
        self.open_sides: dict[str, str] = {}         # symbol -> 'Buy' | 'Sell'
        self.consec_stops = 0
        self.kill_switch_day: str | None = None      # 'YYYY-MM-DD' (UTC), день запрета
        self.cooldown_until: dict[str, str] = {}     # symbol -> ISO-время конца кулдауна
        self._load()

    # ---------- персистентность ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            st = json.loads(self.path.read_text())
            self.consec_stops = st.get("consec_stops", 0)
            self.kill_switch_day = st.get("kill_switch_day")
            self.cooldown_until = st.get("cooldown_until", {})
            log.info("state.json загружен: stops=%d, kill=%s", self.consec_stops, self.kill_switch_day)
        except Exception:
            log.exception("не удалось прочитать state.json — начинаю с чистого состояния")

    def _save(self) -> None:
        self.path.write_text(json.dumps({
            "consec_stops": self.consec_stops,
            "kill_switch_day": self.kill_switch_day,
            "cooldown_until": self.cooldown_until,
        }, indent=2))

    # ---------- события ----------

    def on_trade_closed(self, symbol: str, pnl: float, closed_at: datetime) -> None:
        bar_sec = int(cfg.SIGNAL_TF) * 60
        until = datetime.fromtimestamp(closed_at.timestamp() + cfg.COOLDOWN_BARS * bar_sec, tz=timezone.utc)
        self.cooldown_until[symbol] = until.isoformat()
        if pnl < 0:
            self.consec_stops += 1
            if self.consec_stops >= cfg.MAX_CONSEC_STOPS_PER_DAY:
                self.kill_switch_day = closed_at.strftime("%Y-%m-%d")
                log.warning("KILL SWITCH: %d стопов подряд — торговля остановлена до конца %s (UTC)",
                            self.consec_stops, self.kill_switch_day)
        else:
            self.consec_stops = 0
        self._save()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def maybe_reset_day(self) -> None:
        if self.kill_switch_day and self.kill_switch_day != self._today():
            log.info("новый день UTC — kill switch снят")
            self.kill_switch_day = None
            self.consec_stops = 0
            self._save()

    # ---------- проверка допуска ----------

    def can_open(self, symbol: str, now: datetime, direction: str | None = None) -> tuple[bool, str]:
        self.maybe_reset_day()
        if self.kill_switch_day == self._today():
            return False, "kill switch: дневной лимит стопов исчерпан"
        if symbol in self.open_symbols:
            return False, "уже есть позиция по символу (усреднение запрещено)"
        if len(self.open_symbols) >= cfg.MAX_POS_TOTAL:
            return False, f"открыто {len(self.open_symbols)} позиций (лимит {cfg.MAX_POS_TOTAL})"
        until_iso = self.cooldown_until.get(symbol)
        if until_iso and now < datetime.fromisoformat(until_iso):
            return False, f"кулдаун до {until_iso}"
        if direction is not None:
            # корреляция мажоров: однонаправленные позиции считаем одной ставкой
            side = "Buy" if direction == "long" else "Sell"
            same = sum(1 for s in self.open_sides.values() if s == side)
            if (same + 1) * cfg.RISK_PER_TRADE > cfg.MAX_DIRECTION_RISK + 1e-9:
                return False, (f"риск на направление: {same} открытых {side} x "
                               f"{cfg.RISK_PER_TRADE:.0%} + новая позиция > "
                               f"лимита {cfg.MAX_DIRECTION_RISK:.1%}")
        return True, ""

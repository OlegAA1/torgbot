"""Telegram-уведомления о сигналах и сделках.

Токен и chat_id берутся из .env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID);
если они не заданы — уведомления просто выключены, бот работает как раньше.

Отправка идёт из отдельного потока через очередь: сбой или медленный ответ
Telegram не блокирует главный цикл и не роняет бота.
"""
import logging
import queue
import threading
from datetime import datetime, timedelta, timezone

import requests

from bot import config as cfg
from bot.risk import TradePlan
from bot.signals import SignalCheck

log = logging.getLogger("bot.notifier")

API_URL = "https://api.telegram.org/bot{token}/sendMessage"

NOTIFY_TZ = timezone(timedelta(hours=cfg.NOTIFY_TZ_OFFSET_HOURS))


def _local(dt: datetime) -> str:
    """ЧЧ:ММ:СС в поясе уведомлений (журнал и логи остаются в UTC)."""
    return dt.astimezone(NOTIFY_TZ).strftime("%H:%M:%S")


class Notifier:
    def __init__(self, token: str, chat_id: str):
        self.enabled = bool(token and chat_id)
        self.token = token
        self.chat_id = chat_id
        self._q: queue.Queue[str] = queue.Queue(maxsize=100)
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True, name="notifier").start()
            log.info("Telegram-уведомления включены (chat_id=%s)", chat_id)
        else:
            log.info("Telegram-уведомления выключены (нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID в .env)")

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        text += f"\n🕒 {_local(datetime.now(timezone.utc))} {cfg.NOTIFY_TZ_LABEL}"
        try:
            self._q.put_nowait(text)
        except queue.Full:
            log.warning("очередь уведомлений переполнена, сообщение отброшено")

    def _worker(self) -> None:
        while True:
            text = self._q.get()
            for attempt in (1, 2):
                try:
                    resp = requests.post(
                        API_URL.format(token=self.token),
                        json={"chat_id": self.chat_id, "text": text,
                              "disable_web_page_preview": True},
                        timeout=10,
                    )
                    data = resp.json()
                    if data.get("ok"):
                        break
                    log.warning("Telegram отклонил сообщение: %s", data)
                    break  # ошибка API (не сети) — повтор не поможет
                except Exception as e:
                    log.warning("не удалось отправить в Telegram (попытка %d): %s", attempt, e)


# ---------- форматирование сообщений ----------

def fmt_signal(s: SignalCheck) -> str:
    arrow = "🟢 LONG" if s.direction == "long" else "🔴 SHORT"
    setup = "пробой" if s.setup_type == "breakout" else "отбой"
    candle_close = s.ts.to_pydatetime() + timedelta(minutes=int(s.tf))
    lines = [
        f"{arrow} сигнал {s.symbol} ({s.tf}m, {setup})",
        f"Свеча: {_local(s.ts.to_pydatetime())}–{_local(candle_close)} {cfg.NOTIFY_TZ_LABEL}",
        f"Цена: {s.close}",
        f"Объём: x{s.vol_ratio:.2f} от SMA{cfg.VOL_SMA_PERIOD} ({s.bar_dir}-свеча)",
        f"Уровень: {s.level_kind} {s.level_price:.2f}"
        + (" (пробой)" if s.breakout else f" (дист. {s.level_dist_pct:.2%})"),
        f"MACD: крест {s.cross_dir}/{s.cross_age} св. назад, импульс: {s.hist_impulse or '—'}",
        f"EMA 15m: {cfg.LOCAL_EMA_FAST}={s.ema_fast_15m:.2f} / {cfg.LOCAL_EMA_SLOW}={s.ema_slow_15m:.2f}",
        f"Фильтр 4h: {s.trend_4h} (close {s.close_4h:.2f} vs EMA{cfg.TREND_EMA_PERIOD} {s.ema_4h:.2f}, "
        f"свеча {_local(s.ts_4h.to_pydatetime())} {cfg.NOTIFY_TZ_LABEL})",
    ]
    return "\n".join(lines)


PLAN_HEADS = {
    "dry_run": "📋 [DRY-RUN] Открыта виртуальная позиция (реальный ордер НЕ выставлен)",
    "watch_only": "👀 [WATCH-ONLY] Наблюдаемый символ, торговля по нему выключена",
    "opened": "✅ Открыта сделка (демо)",
}


def fmt_plan(plan: TradePlan, mode: str) -> str:
    head = PLAN_HEADS[mode]
    return (f"{head}\n"
            f"{plan.side} {plan.symbol} qty={plan.qty}\n"
            f"Вход ≈ {plan.entry}\nSL: {plan.stop_loss}\nTP: {plan.take_profit}\n"
            f"Риск: {plan.risk_usdt:.2f} USDT")


def fmt_skip(s: SignalCheck, reason: str) -> str:
    return f"⏸ Сигнал {s.direction} {s.symbol} есть, но сделка не открыта: {reason}"


def fmt_close(symbol: str, side: str, pnl: float, reason: str, duration_min: float | None) -> str:
    emoji = "💰" if pnl >= 0 else "🛑"
    dur = f", {duration_min:.0f} мин" if duration_min is not None else ""
    return f"{emoji} Закрыта сделка {side} {symbol}: PnL {pnl:+.2f} USDT ({reason}{dur})"


def fmt_kill_switch(day: str) -> str:
    return (f"🚫 KILL SWITCH: {cfg.MAX_CONSEC_STOPS_PER_DAY} стопа подряд — "
            f"торговля остановлена до конца {day} (UTC)")

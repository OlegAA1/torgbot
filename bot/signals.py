"""Объединение условий в сигнал long/short.

Все условия проверяются на последней ЗАКРЫТОЙ 15m-свече:
  1) vol_ratio >= порога, причём объём направленный: в лонг засчитывается
     только на бычьей свече, в шорт — на медвежьей;
  2) цена у уровня (long: поддержка ниже цены или пробой сопротивления);
  3) крест MACD в нужную сторону не старше N свечей ИЛИ импульс гистограммы;
  4) фильтр 4h (EMA50) разрешает направление;
  5) локальный тренд 15m: long только при EMA20 > EMA50 (short — наоборот).

check() всегда возвращает SignalCheck со значениями всех факторов —
в журнал пишется каждая проверка, даже без сигнала.
"""
import logging
from dataclasses import dataclass, field

import pandas as pd

from bot import config as cfg
from bot import indicators as ind

log = logging.getLogger("bot.signals")


@dataclass
class SignalCheck:
    ts: pd.Timestamp
    symbol: str
    tf: str
    close: float
    vol_ratio: float
    macd: float
    macd_signal: float
    hist: float
    cross_dir: str | None
    cross_age: int | None
    hist_impulse: str | None
    trend_4h: str            # 'long' | 'short' | 'off'
    atr: float | None = None
    ema_fast_15m: float | None = None
    ema_slow_15m: float | None = None
    ema_4h: float | None = None       # EMA(TREND_EMA_PERIOD) последней закрытой 4h-свечи
    close_4h: float | None = None
    ts_4h: pd.Timestamp | None = None
    bar_dir: str | None = None        # 'bull' | 'bear' | 'flat' — направление свечи
    level_price: float | None = None
    level_kind: str | None = None
    level_dist_pct: float | None = None
    breakout: bool = False
    setup_type: str | None = None     # 'bounce' (отбой) | 'breakout' (пробой)
    tp_obstacle: float | None = None  # ближайший старый пивот по ходу сделки (цель TP)
    price_vs_level: str | None = None # 'above' | 'below'
    direction: str | None = None      # 'long' | 'short' | None — итог
    reasons: list[str] = field(default_factory=list)  # почему сигнала нет


class Deduper:
    """Дедупликация: не повторять сигнал (символ, направление, уровень),
    пока не пройдёт SIGNAL_DEDUP_BARS свечей или цена не отойдёт от уровня
    на SIGNAL_DEDUP_RESET_PCT."""

    def __init__(self):
        self._seen: dict[tuple[str, str, float], pd.Timestamp] = {}

    def release_far(self, symbol: str, close: float) -> None:
        """Вызывать на каждой закрытой свече: снимает блок, если цена ушла от уровня."""
        for key in list(self._seen):
            sym, _, level = key
            if sym == symbol and abs(close - level) / level > cfg.SIGNAL_DEDUP_RESET_PCT:
                del self._seen[key]

    def is_dup(self, symbol: str, direction: str, level: float, ts: pd.Timestamp) -> bool:
        key = (symbol, direction, round(level, 8))
        first = self._seen.get(key)
        bar = pd.Timedelta(minutes=int(cfg.SIGNAL_TF))
        if first is not None and ts - first < cfg.SIGNAL_DEDUP_BARS * bar:
            return True
        self._seen[key] = ts
        return False


def _level_context(df: pd.DataFrame, direction: str) -> tuple[ind.Level | None, float | None, bool]:
    """Уровень для направления: (уровень, дистанция, был ли пробой).

    long: подход к поддержке ИЛИ пробой сопротивления (prev close ниже, close выше).
    short — зеркально.
    """
    levels = ind.sr_levels(df)
    close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])

    near_kind = "support" if direction == "long" else "resistance"
    lv, dist = ind.nearest_level(levels, close, near_kind)
    if lv is not None and dist <= cfg.SR_PROXIMITY_PCT:
        return lv, dist, False

    brk_kind = "resistance" if direction == "long" else "support"
    for b in levels:
        if b.kind != brk_kind:
            continue
        if direction == "long" and prev_close < b.price < close:
            return b, abs(close - b.price) / b.price, True
        if direction == "short" and close < b.price < prev_close:
            return b, abs(close - b.price) / b.price, True
    return lv, (dist if lv is not None else None), False


def trend_filter(df_4h: pd.DataFrame) -> tuple[str, float, float, pd.Timestamp]:
    """(разрешённое направление | 'off', EMA, close, ts последней закрытой 4h-свечи)."""
    close = float(df_4h["close"].iloc[-1])
    e = float(ind.ema(df_4h["close"], cfg.TREND_EMA_PERIOD).iloc[-1])
    ts = df_4h.index[-1]
    if not cfg.USE_TREND_FILTER:
        return "off", e, close, ts
    return ("long" if close > e else "short"), e, close, ts


def check(symbol: str, df_15: pd.DataFrame, df_4h: pd.DataFrame) -> SignalCheck:
    m = ind.macd(df_15["close"])
    vr = float(ind.vol_ratio(df_15["volume"]).iloc[-1])
    cross_dir, cross_age = ind.macd_cross_age(m)
    close = float(df_15["close"].iloc[-1])
    bar_open = float(df_15["open"].iloc[-1])
    impulse = ind.hist_impulse(m, close)
    trend, ema_4h, close_4h, ts_4h = trend_filter(df_4h)
    atr = float(ind.atr(df_15).iloc[-1])
    ema_fast = float(ind.ema(df_15["close"], cfg.LOCAL_EMA_FAST).iloc[-1])
    ema_slow = float(ind.ema(df_15["close"], cfg.LOCAL_EMA_SLOW).iloc[-1])
    bar_dir = "bull" if close > bar_open else ("bear" if close < bar_open else "flat")

    # диагностика 4h-фильтра: свеча старше двух периодов = данные не обновляются
    if df_15.index[-1] - ts_4h > pd.Timedelta(minutes=2 * int(cfg.TREND_TF)):
        log.warning("%s: последняя 4h-свеча слишком старая (%s) — фильтр 4h может врать",
                    symbol, ts_4h)

    res = SignalCheck(
        ts=df_15.index[-1], symbol=symbol, tf=cfg.SIGNAL_TF,
        close=close, vol_ratio=vr,
        macd=float(m["macd"].iloc[-1]), macd_signal=float(m["signal"].iloc[-1]),
        hist=float(m["hist"].iloc[-1]),
        cross_dir=cross_dir, cross_age=cross_age,
        hist_impulse=impulse, trend_4h=trend,
        atr=atr, ema_fast_15m=ema_fast, ema_slow_15m=ema_slow,
        ema_4h=ema_4h, close_4h=close_4h, ts_4h=ts_4h, bar_dir=bar_dir,
    )

    if pd.isna(vr) or vr < cfg.VOL_RATIO_THRESHOLD:
        res.reasons.append(f"vol_ratio {vr:.2f} < {cfg.VOL_RATIO_THRESHOLD}")
        return res

    fresh_cross_up = cross_dir == "up" and cross_age is not None and cross_age <= cfg.MACD_CROSS_MAX_AGE
    fresh_cross_dn = cross_dir == "down" and cross_age is not None and cross_age <= cfg.MACD_CROSS_MAX_AGE
    macd_long = fresh_cross_up or impulse == "bull"
    macd_short = fresh_cross_dn or impulse == "bear"

    for direction, macd_ok in (("long", macd_long), ("short", macd_short)):
        if trend not in ("off", direction):
            res.reasons.append(f"{direction}: запрещён фильтром 4h ({trend})")
            continue
        if cfg.USE_LOCAL_TREND_FILTER:
            if direction == "long" and ema_fast <= ema_slow:
                res.reasons.append(
                    f"long: EMA{cfg.LOCAL_EMA_FAST} <= EMA{cfg.LOCAL_EMA_SLOW} на 15m")
                continue
            if direction == "short" and ema_fast >= ema_slow:
                res.reasons.append(
                    f"short: EMA{cfg.LOCAL_EMA_FAST} >= EMA{cfg.LOCAL_EMA_SLOW} на 15m")
                continue
        # направление объёма: аномальный объём должен совпадать со свечой
        if direction == "long" and bar_dir != "bull":
            res.reasons.append(f"long: аномальный объём на {bar_dir}-свече")
            continue
        if direction == "short" and bar_dir != "bear":
            res.reasons.append(f"short: аномальный объём на {bar_dir}-свече")
            continue
        if not macd_ok:
            res.reasons.append(f"{direction}: нет креста MACD (<= {cfg.MACD_CROSS_MAX_AGE} св.) и нет импульса")
            continue
        lv, dist, breakout = _level_context(df_15, direction)
        if lv is None or dist is None or (not breakout and dist > cfg.SR_PROXIMITY_PCT):
            res.reasons.append(
                f"{direction}: нет уровня рядом"
                + (f" (ближайший {lv.kind} {lv.price:.2f}, {dist:.2%})" if lv else "")
            )
            continue
        if breakout and dist > cfg.BREAKOUT_MAX_DIST_PCT:
            res.reasons.append(
                f"{direction}: пробой поздний — закрытие в {dist:.2%} от уровня "
                f"(лимит {cfg.BREAKOUT_MAX_DIST_PCT:.1%})")
            continue
        res.direction = direction
        res.level_price, res.level_kind = lv.price, lv.kind
        res.level_dist_pct, res.breakout = dist, breakout
        res.setup_type = "breakout" if breakout else "bounce"
        res.price_vs_level = "above" if close > lv.price else "below"
        res.tp_obstacle = ind.nearest_obstacle(df_15, close, direction)
        break

    return res

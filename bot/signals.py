"""Объединение условий в сигнал long/short.

Все условия проверяются на последней ЗАКРЫТОЙ 15m-свече:
  1) vol_ratio >= порога;
  2) цена у уровня (long: поддержка или пробой сопротивления с закрытием выше);
  3) крест MACD в нужную сторону не старше N свечей ИЛИ импульс гистограммы;
  4) фильтр 4h (EMA50) разрешает направление.

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
    level_price: float | None = None
    level_kind: str | None = None
    level_dist_pct: float | None = None
    breakout: bool = False
    direction: str | None = None   # 'long' | 'short' | None — итог
    reasons: list[str] = field(default_factory=list)  # почему сигнала нет


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


def trend_filter(df_4h: pd.DataFrame) -> str:
    """'long' | 'short' — какое направление разрешает 4h, 'off' если фильтр выключен."""
    if not cfg.USE_TREND_FILTER:
        return "off"
    close = float(df_4h["close"].iloc[-1])
    e = float(ind.ema(df_4h["close"], cfg.TREND_EMA_PERIOD).iloc[-1])
    return "long" if close > e else "short"


def check(symbol: str, df_15: pd.DataFrame, df_4h: pd.DataFrame) -> SignalCheck:
    m = ind.macd(df_15["close"])
    vr = float(ind.vol_ratio(df_15["volume"]).iloc[-1])
    cross_dir, cross_age = ind.macd_cross_age(m)
    impulse = ind.hist_impulse(m)
    trend = trend_filter(df_4h)

    res = SignalCheck(
        ts=df_15.index[-1], symbol=symbol, tf=cfg.SIGNAL_TF,
        close=float(df_15["close"].iloc[-1]), vol_ratio=vr,
        macd=float(m["macd"].iloc[-1]), macd_signal=float(m["signal"].iloc[-1]),
        hist=float(m["hist"].iloc[-1]),
        cross_dir=cross_dir, cross_age=cross_age,
        hist_impulse=impulse, trend_4h=trend,
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
        res.direction = direction
        res.level_price, res.level_kind = lv.price, lv.kind
        res.level_dist_pct, res.breakout = dist, breakout
        break

    return res

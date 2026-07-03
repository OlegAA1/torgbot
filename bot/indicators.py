"""Индикаторы: объём/SMA, EMA, MACD(12,26,9), гистограмма, фрактальные уровни S/R.

Все функции работают с pandas и не имеют побочных эффектов.
Вся логика считается ТОЛЬКО по закрытым свечам — незакрытую свечу в df не передавать.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot import config as cfg


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def macd(close: pd.Series) -> pd.DataFrame:
    """MACD на EMA. Колонки: macd, signal, hist."""
    line = ema(close, cfg.MACD_FAST) - ema(close, cfg.MACD_SLOW)
    sig = line.ewm(span=cfg.MACD_SIGNAL, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def vol_ratio(volume: pd.Series) -> pd.Series:
    """volume / SMA(volume, VOL_SMA_PERIOD)."""
    sma = volume.rolling(cfg.VOL_SMA_PERIOD).mean()
    return volume / sma


def macd_cross_age(m: pd.DataFrame) -> tuple[str | None, int | None]:
    """Последний крест macd/signal: ('up'|'down', возраст в свечах от последней закрытой).

    Возраст 0 = крест на последней закрытой свече.
    """
    diff = np.sign((m["macd"] - m["signal"]).to_numpy())
    for age in range(len(diff) - 1):
        i = len(diff) - 1 - age
        if diff[i] != 0 and diff[i - 1] != 0 and diff[i] != diff[i - 1]:
            return ("up" if diff[i] > 0 else "down"), age
    return None, None


def hist_impulse(m: pd.DataFrame) -> str | None:
    """Импульс гистограммы: 'bull' | 'bear' | None.

    Условие: |hist| растёт HIST_IMPULSE_BARS свечей подряд
    ИЛИ |hist| > HIST_IMPULSE_MULT * средний |hist| за HIST_AVG_PERIOD.
    Направление — по знаку текущей гистограммы.
    """
    h = m["hist"]
    if len(h) < cfg.HIST_AVG_PERIOD + 1:
        return None
    last = h.iloc[-1]
    if last == 0 or np.isnan(last):
        return None

    abs_h = h.abs()
    tail = abs_h.iloc[-(cfg.HIST_IMPULSE_BARS + 1):].to_numpy()
    rising = bool(np.all(np.diff(tail) > 0))
    avg = abs_h.iloc[-cfg.HIST_AVG_PERIOD:].mean()
    spike = bool(avg > 0 and abs_h.iloc[-1] > cfg.HIST_IMPULSE_MULT * avg)

    if rising or spike:
        return "bull" if last > 0 else "bear"
    return None


@dataclass
class Level:
    price: float
    kind: str      # 'support' (пивот-low) или 'resistance' (пивот-high)
    touches: int   # сколько экстремумов слилось в этот уровень


def sr_levels(df: pd.DataFrame) -> list[Level]:
    """Фрактальные уровни за последние SR_LOOKBACK свечей.

    Пивот: high выше SR_FRACTAL_WING соседей слева и справа (аналогично low).
    Уровни ближе SR_CLUSTER_PCT сливаются в один (среднее по кластеру).
    """
    w = cfg.SR_FRACTAL_WING
    d = df.iloc[-cfg.SR_LOOKBACK:]
    highs, lows = d["high"].to_numpy(), d["low"].to_numpy()

    raw: list[tuple[float, str]] = []
    for i in range(w, len(d) - w):
        if highs[i] == highs[i - w:i + w + 1].max() and (highs[i] > highs[i - w:i]).all() and (highs[i] > highs[i + 1:i + w + 1]).all():
            raw.append((float(highs[i]), "resistance"))
        if lows[i] == lows[i - w:i + w + 1].min() and (lows[i] < lows[i - w:i]).all() and (lows[i] < lows[i + 1:i + w + 1]).all():
            raw.append((float(lows[i]), "support"))

    raw.sort(key=lambda x: x[0])
    levels: list[Level] = []
    cluster: list[tuple[float, str]] = []

    def flush() -> None:
        if not cluster:
            return
        prices = [p for p, _ in cluster]
        kinds = [k for _, k in cluster]
        # тип кластера — по большинству экстремумов в нём
        kind = "support" if kinds.count("support") >= kinds.count("resistance") else "resistance"
        levels.append(Level(price=float(np.mean(prices)), kind=kind, touches=len(cluster)))

    for p, k in raw:
        if cluster and (p - cluster[0][0]) / cluster[0][0] > cfg.SR_CLUSTER_PCT:
            flush()
            cluster = []
        cluster.append((p, k))
    flush()
    return levels


def nearest_level(levels: list[Level], price: float, kind: str) -> tuple[Level | None, float]:
    """Ближайший уровень заданного типа и относительная дистанция |price-level|/level."""
    best, best_dist = None, float("inf")
    for lv in levels:
        if lv.kind != kind:
            continue
        dist = abs(price - lv.price) / lv.price
        if dist < best_dist:
            best, best_dist = lv, dist
    return best, best_dist

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


def atr(df: pd.DataFrame, period: int | None = None) -> pd.Series:
    """ATR: SMA истинного диапазона за period свечей."""
    period = period or cfg.ATR_PERIOD
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


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


def hist_impulse(m: pd.DataFrame, close: float) -> str | None:
    """Импульс гистограммы: 'bull' | 'bear' | None.

    bull: hist > 0, растёт HIST_IMPULSE_BARS свечей подряд, и линия MACD
    выше нуля либо не глубже MACD_NEAR_ZERO_PCT*цена под ним — один тик
    вверх в глубокой медвежьей зоне импульсом не считается. bear — зеркально.
    """
    h = m["hist"]
    if len(h) < cfg.HIST_IMPULSE_BARS + 1:
        return None
    last = float(h.iloc[-1])
    if last == 0 or np.isnan(last):
        return None

    tail = h.iloc[-(cfg.HIST_IMPULSE_BARS + 1):].to_numpy()
    line = float(m["macd"].iloc[-1])
    zero_tol = cfg.MACD_NEAR_ZERO_PCT * close

    if last > 0 and bool(np.all(np.diff(tail) > 0)) and line > -zero_tol:
        return "bull"
    if last < 0 and bool(np.all(np.diff(tail) < 0)) and line < zero_tol:
        return "bear"
    return None


@dataclass
class Level:
    price: float
    kind: str      # 'support' (пивот-low) или 'resistance' (пивот-high)
    touches: int   # сколько экстремумов слилось в этот уровень
    age: int = 0   # свечей от последнего пивота кластера до последней закрытой


def _fractal_pivots(d: pd.DataFrame) -> list[tuple[float, str, int]]:
    """Фрактальные экстремумы: (цена, 'resistance'|'support', индекс бара в d)."""
    w = cfg.SR_FRACTAL_WING
    highs, lows = d["high"].to_numpy(), d["low"].to_numpy()
    raw: list[tuple[float, str, int]] = []
    for i in range(w, len(d) - w):
        if highs[i] == highs[i - w:i + w + 1].max() and (highs[i] > highs[i - w:i]).all() and (highs[i] > highs[i + 1:i + w + 1]).all():
            raw.append((float(highs[i]), "resistance", i))
        if lows[i] == lows[i - w:i + w + 1].min() and (lows[i] < lows[i - w:i]).all() and (lows[i] < lows[i + 1:i + w + 1]).all():
            raw.append((float(lows[i]), "support", i))
    return raw


def sr_levels(df: pd.DataFrame) -> list[Level]:
    """Фрактальные уровни за последние SR_LOOKBACK свечей.

    Пивот: high выше SR_FRACTAL_WING соседей слева и справа (аналогично low).
    Уровни ближе SR_CLUSTER_PCT сливаются в один (среднее по кластеру).

    Жизненный цикл: уровень старше SR_LEVEL_MAX_AGE_BARS отбрасывается;
    уровень, пробитый закрытием свечи после формирования, меняет роль
    (пробитая поддержка -> сопротивление и наоборот). Последняя закрытая
    свеча в переклассификации не участвует — её пробой обрабатывается
    в сигнальной логике как breakout.
    """
    d = df.iloc[-cfg.SR_LOOKBACK:]
    closes = d["close"].to_numpy()
    n = len(d)

    raw = sorted(_fractal_pivots(d), key=lambda x: x[0])
    levels: list[Level] = []
    cluster: list[tuple[float, str, int]] = []

    def flush() -> None:
        if not cluster:
            return
        prices = [p for p, _, _ in cluster]
        kinds = [k for _, k, _ in cluster]
        last_idx = max(i for _, _, i in cluster)
        age = n - 1 - last_idx
        if age > cfg.SR_LEVEL_MAX_AGE_BARS:
            return  # древний уровень из старой структуры
        # тип кластера — по большинству экстремумов в нём
        kind = "support" if kinds.count("support") >= kinds.count("resistance") else "resistance"
        price = float(np.mean(prices))
        # переклассификация по закрытиям после формирования (кроме последней свечи)
        for c in closes[last_idx + 1:n - 1]:
            if kind == "support" and c < price:
                kind = "resistance"
            elif kind == "resistance" and c > price:
                kind = "support"
        levels.append(Level(price=price, kind=kind, touches=len(cluster), age=age))

    for p, k, i in raw:
        if cluster and (p - cluster[0][0]) / cluster[0][0] > cfg.SR_CLUSTER_PCT:
            flush()
            cluster = []
        cluster.append((p, k, i))
    flush()
    return levels


def nearest_obstacle(df: pd.DataFrame, price: float, direction: str) -> float | None:
    """Ближайшее препятствие для тейка: кластер пивот-high выше цены (long)
    или пивот-low ниже цены (short).

    В отличие от sr_levels — без срока жизни и переклассификации: для тейка
    старые уровни как раз важны (память рынка), а фильтровать входы они не могут.
    """
    d = df.iloc[-cfg.SR_LOOKBACK:]
    kind = "resistance" if direction == "long" else "support"
    prices = sorted(p for p, k, _ in _fractal_pivots(d) if k == kind)
    if not prices:
        return None

    clusters: list[float] = []
    cluster: list[float] = []
    for p in prices:
        if cluster and (p - cluster[0]) / cluster[0] > cfg.SR_CLUSTER_PCT:
            clusters.append(float(np.mean(cluster)))
            cluster = []
        cluster.append(p)
    clusters.append(float(np.mean(cluster)))

    if direction == "long":
        ups = [c for c in clusters if c > price]
        return min(ups) if ups else None
    downs = [c for c in clusters if c < price]
    return max(downs) if downs else None


def nearest_level(levels: list[Level], price: float, kind: str) -> tuple[Level | None, float]:
    """Ближайший уровень заданного типа и относительная дистанция |price-level|/level.

    Сторона обязательна: support учитывается только при цене ВЫШЕ уровня,
    resistance — только при цене НИЖЕ. Лонг под пробитой поддержкой невозможен.
    """
    best, best_dist = None, float("inf")
    for lv in levels:
        if lv.kind != kind:
            continue
        if kind == "support" and price <= lv.price:
            continue
        if kind == "resistance" and price >= lv.price:
            continue
        dist = abs(price - lv.price) / lv.price
        if dist < best_dist:
            best, best_dist = lv, dist
    return best, best_dist

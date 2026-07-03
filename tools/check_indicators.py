"""Шаг 8.1 из ТЗ: юнит-проверка индикаторов.

Печатает последние значения EMA/MACD/vol_ratio по реальной истории —
сверить вручную с графиком TradingView/Bybit на тех же свечах (UTC!).
Плюс самопроверка MACD на синтетическом ряде против прямого расчёта EMA.

Запуск:  python -m tools.check_indicators [SYMBOL]
"""
import sys

import numpy as np
import pandas as pd
from pybit.unified_trading import HTTP

from bot import config as cfg
from bot import indicators as ind
from bot.data import klines_to_df


def self_test_macd() -> None:
    """MACD должен совпадать с прямым расчётом EMA по определению."""
    rng = np.random.default_rng(42)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 300)))
    m = ind.macd(close)

    def ema_ref(s, n):
        a = 2 / (n + 1)
        out = [s.iloc[0]]
        for x in s.iloc[1:]:
            out.append(a * x + (1 - a) * out[-1])
        return pd.Series(out, index=s.index)

    line_ref = ema_ref(close, cfg.MACD_FAST) - ema_ref(close, cfg.MACD_SLOW)
    sig_ref = ema_ref(line_ref, cfg.MACD_SIGNAL)
    assert np.allclose(m["macd"], line_ref, atol=1e-9), "MACD line mismatch"
    assert np.allclose(m["signal"], sig_ref, atol=1e-9), "MACD signal mismatch"
    print("самопроверка MACD против эталонного EMA: OK")


def show_real(symbol: str) -> None:
    http = HTTP(demo=cfg.DEMO)
    resp = http.get_kline(category=cfg.CATEGORY, symbol=symbol,
                          interval=cfg.SIGNAL_TF, limit=cfg.HISTORY_LIMIT)
    df = klines_to_df(resp["result"]["list"]).iloc[:-1]  # только закрытые

    m = ind.macd(df["close"])
    vr = ind.vol_ratio(df["volume"])
    out = pd.DataFrame({
        "close": df["close"], "volume": df["volume"], "vol_ratio": vr.round(2),
        "macd": m["macd"].round(2), "signal": m["signal"].round(2), "hist": m["hist"].round(2),
        "ema50": ind.ema(df["close"], cfg.TREND_EMA_PERIOD).round(2),
    })
    print(f"\n{symbol} {cfg.SIGNAL_TF}m, последние 10 закрытых свечей (время UTC):")
    print(out.tail(10).to_string())

    levels = ind.sr_levels(df)
    print(f"\nУровни S/R (кластеризация {cfg.SR_CLUSTER_PCT:.1%}):")
    for lv in sorted(levels, key=lambda x: -x.price):
        print(f"  {lv.kind:<10} {lv.price:>12.2f}  (экстремумов: {lv.touches})")

    print("\nСверить macd/signal/hist и ema50 с TradingView (MACD 12/26/9, EMA 50) на тех же свечах.")


if __name__ == "__main__":
    self_test_macd()
    show_real(sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT")

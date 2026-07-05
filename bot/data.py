"""Данные: REST-загрузка свечей + WebSocket-подписка на kline.

Хранит DataFrame'ы ТОЛЬКО из закрытых свечей. WS-бары с confirm=False игнорируются.
Время везде UTC (индекс — pd.Timestamp, tz='UTC').
"""
import logging
import queue
import threading
import time

import pandas as pd
from pybit.unified_trading import HTTP, WebSocket

from bot import config as cfg

log = logging.getLogger("bot.data")

COLS = ["open", "high", "low", "close", "volume", "turnover"]


def klines_to_df(rows: list) -> pd.DataFrame:
    """Ответ /v5/market/kline (новые сверху) -> DataFrame по возрастанию времени."""
    df = pd.DataFrame(rows, columns=["start", *COLS])
    df["start"] = pd.to_datetime(df["start"].astype("int64"), unit="ms", utc=True)
    df[COLS] = df[COLS].astype(float)
    return df.set_index("start").sort_index()


class MarketData:
    """Свечи по всем символам/ТФ + очередь событий «закрылась 15m-свеча»."""

    def __init__(self, http: HTTP):
        self.http = http
        self.frames: dict[tuple[str, str], pd.DataFrame] = {}
        self.closed_bars: queue.Queue[tuple[str, str]] = queue.Queue()
        self._lock = threading.Lock()
        self._ws: WebSocket | None = None
        self._last_ws_msg = time.time()
        # (symbol, interval) -> start последнего поставленного в очередь бара:
        # WS после реконнекта/повторной подписки может прислать бар дважды,
        # что раньше давало двойную отправку одинаковых сигналов
        self._last_queued: dict[tuple[str, str], int] = {}

    # ---------- REST ----------

    def load_history(self) -> None:
        for symbol in cfg.ALL_SYMBOLS:
            for interval in (cfg.SIGNAL_TF, cfg.TREND_TF):
                resp = self.http.get_kline(
                    category=cfg.CATEGORY, symbol=symbol,
                    interval=interval, limit=cfg.HISTORY_LIMIT,
                )
                rows = resp["result"]["list"]
                df = klines_to_df(rows)
                # последняя свеча в ответе ещё не закрыта — отбрасываем
                df = df.iloc[:-1]
                with self._lock:
                    self.frames[(symbol, interval)] = df
                log.info("история %s %sm: %d закрытых свечей, последняя %s",
                         symbol, interval, len(df), df.index[-1])

    def df(self, symbol: str, interval: str) -> pd.DataFrame:
        with self._lock:
            return self.frames[(symbol, interval)].copy()

    # ---------- WebSocket ----------

    def start_ws(self) -> None:
        self._ws = WebSocket(
            testnet=False, demo=cfg.WS_PUBLIC_DEMO, channel_type=cfg.CATEGORY,
            retries=20, restart_on_error=True,
        )
        for symbol in cfg.ALL_SYMBOLS:
            for interval in (cfg.SIGNAL_TF, cfg.TREND_TF):
                self._ws.kline_stream(
                    interval=int(interval), symbol=symbol,
                    callback=self._on_kline,
                )
        log.info("WebSocket: подписка на kline %s x %s",
                 cfg.ALL_SYMBOLS, [cfg.SIGNAL_TF, cfg.TREND_TF])

    def _on_kline(self, msg: dict) -> None:
        self._last_ws_msg = time.time()
        try:
            topic = msg.get("topic", "")            # kline.15.BTCUSDT
            _, interval, symbol = topic.split(".")
            for bar in msg.get("data", []):
                if not bar.get("confirm"):
                    continue  # сигналы по незакрытой свече запрещены
                key, start = (symbol, interval), int(bar["start"])
                if self._last_queued.get(key, -1) >= start:
                    log.info("дубль закрытого бара %s %sm start=%s — пропущен", symbol, interval, start)
                    continue
                self._last_queued[key] = start
                self._append_bar(symbol, interval, bar)
                self.closed_bars.put((symbol, interval))
        except Exception:
            log.exception("ошибка обработки WS-сообщения: %s", msg)

    def _append_bar(self, symbol: str, interval: str, bar: dict) -> None:
        ts = pd.to_datetime(int(bar["start"]), unit="ms", utc=True)
        row = [float(bar[k]) for k in COLS]
        with self._lock:
            df = self.frames[(symbol, interval)]
            df.loc[ts] = row                      # дедуп по индексу: повтор перезапишет
            df.sort_index(inplace=True)
            if len(df) > cfg.HISTORY_LIMIT * 2:   # не растим память бесконечно
                self.frames[(symbol, interval)] = df.iloc[-cfg.HISTORY_LIMIT:]

    def ws_alive(self) -> bool:
        return time.time() - self._last_ws_msg < cfg.WS_STALE_SEC

    def restart_ws(self) -> None:
        log.warning("WebSocket молчит > %ds — реконнект", cfg.WS_STALE_SEC)
        try:
            if self._ws:
                self._ws.exit()
        except Exception:
            log.exception("ошибка при закрытии WS")
        self.load_history()   # добираем пропущенные свечи через REST
        self.start_ws()
        self._last_ws_msg = time.time()

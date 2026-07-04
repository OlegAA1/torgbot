"""Исполнение через Bybit API v5 (демо-среда, category=linear).

SL/TP ставятся сразу в place_order. Бот никогда не модифицирует стоп после входа.
Любая ошибка API логируется, бот не падает.
"""
import logging
import time
from decimal import Decimal

from pybit.unified_trading import HTTP

from bot import config as cfg
from bot.risk import TradePlan

log = logging.getLogger("bot.executor")


class ApiError(Exception):
    pass


class Executor:
    def __init__(self, http: HTTP):
        self.http = http
        self.instruments: dict[str, dict] = {}

    def _call(self, name: str, fn, **kwargs):
        """Вызов API с проверкой retCode и одним повтором на сетевой ошибке."""
        for attempt in (1, 2):
            try:
                resp = fn(**kwargs)
                if resp.get("retCode") != 0:
                    raise ApiError(f"{name}: retCode={resp.get('retCode')} {resp.get('retMsg')}")
                return resp["result"]
            except ApiError:
                raise
            except Exception as e:
                log.warning("%s: сетевая ошибка (попытка %d): %s", name, attempt, e)
                if attempt == 2:
                    raise
                time.sleep(2)

    # ---------- справочная информация ----------

    def load_instruments(self) -> list[str]:
        """Загружает фильтры инструментов. Возвращает символы, которых нет на бирже."""
        missing: list[str] = []
        for symbol in cfg.ALL_SYMBOLS:
            try:
                res = self._call("get_instruments_info", self.http.get_instruments_info,
                                 category=cfg.CATEGORY, symbol=symbol)
                if not res["list"]:
                    raise ApiError(f"{symbol}: биржа не знает такой символ")
                info = res["list"][0]
            except Exception as e:
                log.warning("инструмент %s недоступен (%s) — исключаю из работы", symbol, e)
                missing.append(symbol)
                continue
            self.instruments[symbol] = {
                "qty_step": float(info["lotSizeFilter"]["qtyStep"]),
                "min_qty": float(info["lotSizeFilter"]["minOrderQty"]),
                "tick_size": float(info["priceFilter"]["tickSize"]),
            }
            log.info("%s: qtyStep=%s minQty=%s tick=%s", symbol,
                     self.instruments[symbol]["qty_step"],
                     self.instruments[symbol]["min_qty"],
                     self.instruments[symbol]["tick_size"])
        return missing

    def balance_usdt(self) -> float:
        res = self._call("get_wallet_balance", self.http.get_wallet_balance,
                         accountType="UNIFIED")
        return float(res["list"][0]["totalEquity"])

    def open_positions(self) -> dict[str, dict]:
        """symbol -> позиция (только наши символы, size > 0)."""
        res = self._call("get_positions", self.http.get_positions,
                         category=cfg.CATEGORY, settleCoin="USDT")
        out = {}
        for p in res["list"]:
            if p["symbol"] in cfg.SYMBOLS and float(p["size"]) > 0:
                out[p["symbol"]] = p
        return out

    def closed_pnl(self, symbol: str, limit: int = 10) -> list[dict]:
        res = self._call("get_closed_pnl", self.http.get_closed_pnl,
                         category=cfg.CATEGORY, symbol=symbol, limit=limit)
        return res["list"]

    # ---------- ордера ----------

    def _fmt_price(self, symbol: str, price: float) -> str:
        tick = Decimal(str(self.instruments[symbol]["tick_size"]))
        return str((Decimal(str(price)) / tick).quantize(Decimal("1")) * tick)

    def _fmt_qty(self, symbol: str, qty: float) -> str:
        step = Decimal(str(self.instruments[symbol]["qty_step"]))
        return str((Decimal(str(qty)) / step).to_integral_value(rounding="ROUND_DOWN") * step)

    def place_market(self, plan: TradePlan) -> str | None:
        """Market-ордер с SL/TP. Возвращает orderId или None при ошибке."""
        try:
            res = self._call(
                "place_order", self.http.place_order,
                category=cfg.CATEGORY,
                symbol=plan.symbol,
                side=plan.side,
                orderType="Market",
                qty=self._fmt_qty(plan.symbol, plan.qty),
                stopLoss=self._fmt_price(plan.symbol, plan.stop_loss),
                takeProfit=self._fmt_price(plan.symbol, plan.take_profit),
                positionIdx=0,  # one-way mode
            )
            order_id = res["orderId"]
            log.info("ордер выставлен: %s %s qty=%s SL=%s TP=%s id=%s",
                     plan.symbol, plan.side, plan.qty, plan.stop_loss, plan.take_profit, order_id)
            return order_id
        except Exception:
            log.exception("не удалось выставить ордер %s %s", plan.symbol, plan.side)
            return None

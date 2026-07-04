"""Главный цикл: данные -> индикаторы -> сигнал -> (dry-run | ордер).

Запуск:
    python -m bot.main            # рабочий режим (WebSocket, ждёт закрытия свечей)
    python -m bot.main --once     # разовый прогон по истории (проверка пайплайна)

Режим торговли переключается в config.py: DRY_RUN = True/False.
"""
import argparse
import logging
import queue
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

from bot import config as cfg
from bot import notifier, risk, signals
from bot.data import MarketData
from bot.executor import Executor
from bot.journal import Journal, utcnow

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("bot")


def setup_logging() -> None:
    log_dir = ROOT / cfg.LOG_DIR
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fmt.converter = time.gmtime  # время в логах — UTC

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    main_file = logging.FileHandler(log_dir / "bot.log", encoding="utf-8")
    main_file.setFormatter(fmt)
    root.addHandler(main_file)

    err_file = logging.FileHandler(log_dir / "errors.log", encoding="utf-8")
    err_file.setLevel(logging.ERROR)
    err_file.setFormatter(fmt)
    root.addHandler(err_file)


class Bot:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        import os
        api_key = os.getenv("API_KEY", "")
        api_secret = os.getenv("API_SECRET", "")
        self.has_keys = bool(api_key and api_secret)
        if not self.has_keys:
            log.warning("API-ключи не заданы (.env) — доступны только публичные данные, торговля невозможна")
        if not cfg.DRY_RUN and not self.has_keys:
            raise SystemExit("DRY_RUN=False, но ключей нет — заполните .env")

        self.http = HTTP(demo=cfg.DEMO, api_key=api_key or None, api_secret=api_secret or None)
        self.notify = notifier.Notifier(os.getenv("TELEGRAM_BOT_TOKEN", ""),
                                        os.getenv("TELEGRAM_CHAT_ID", ""))
        self.market = MarketData(self.http)
        self.executor = Executor(self.http)
        self.journal = Journal(ROOT / cfg.JOURNAL_DIR)
        self.risk = risk.RiskState(ROOT / cfg.STATE_FILE)
        # symbol -> метаданные открытой сделки (для итога в trades.csv)
        self.tracked: dict[str, dict] = {}
        self._last_sync = 0.0

    # ---------- позиции ----------

    def sync_positions(self) -> None:
        """Сверка с биржей: подхват позиций после рестарта + фиксация закрытий."""
        if not self.has_keys:
            return
        try:
            on_exchange = self.executor.open_positions()
        except Exception:
            log.exception("не удалось получить позиции")
            return

        for symbol, p in on_exchange.items():
            if symbol not in self.tracked:
                self.tracked[symbol] = {
                    "side": p["side"],
                    "qty": float(p["size"]),
                    "entry": float(p["avgPrice"]),
                    "sl": float(p["stopLoss"]) if p.get("stopLoss") else None,
                    "tp": float(p["takeProfit"]) if p.get("takeProfit") else None,
                    "opened_ts": datetime.fromtimestamp(
                        int(p["createdTime"]) / 1000, tz=timezone.utc),
                }
                log.info("подхвачена позиция с биржи: %s %s qty=%s entry=%s",
                         symbol, p["side"], p["size"], p["avgPrice"])

        for symbol in list(self.tracked):
            if symbol in on_exchange:
                continue
            self._register_close(symbol, self.tracked.pop(symbol))

        self.risk.open_symbols = set(on_exchange)

    def _register_close(self, symbol: str, meta: dict) -> None:
        closed_at = utcnow()
        pnl, exit_price, reason = 0.0, 0.0, "unknown"
        try:
            for rec in self.executor.closed_pnl(symbol):
                pnl = float(rec["closedPnl"])
                exit_price = float(rec["avgExitPrice"])
                closed_at = datetime.fromtimestamp(int(rec["updatedTime"]) / 1000, tz=timezone.utc)
                reason = self._close_reason(exit_price, meta)
                break
        except Exception:
            log.exception("не удалось получить closed-pnl по %s", symbol)
        self.journal.log_trade_closed(
            opened_ts=meta.get("opened_ts"), closed_ts=closed_at,
            symbol=symbol, side=meta["side"], qty=meta["qty"],
            entry=meta["entry"], exit_price=exit_price,
            sl=meta.get("sl"), tp=meta.get("tp"),
            pnl=pnl, close_reason=reason,
        )
        kill_before = self.risk.kill_switch_day
        self.risk.on_trade_closed(symbol, pnl, closed_at)
        if cfg.NOTIFY_TRADES:
            duration = None
            if meta.get("opened_ts"):
                duration = (closed_at - meta["opened_ts"]).total_seconds() / 60
            self.notify.send(notifier.fmt_close(symbol, meta["side"], pnl, reason, duration))
            if self.risk.kill_switch_day and self.risk.kill_switch_day != kill_before:
                self.notify.send(notifier.fmt_kill_switch(self.risk.kill_switch_day))

    @staticmethod
    def _close_reason(exit_price: float, meta: dict) -> str:
        for name in ("sl", "tp"):
            ref = meta.get(name)
            if ref and abs(exit_price - ref) / ref < 0.002:
                return name.upper()
        return "manual/other"

    # ---------- обработка закрытой свечи ----------

    def on_closed_bar(self, symbol: str) -> None:
        df15 = self.market.df(symbol, cfg.SIGNAL_TF)
        df4h = self.market.df(symbol, cfg.TREND_TF)
        s = signals.check(symbol, df15, df4h)

        if s.direction is None:
            self.journal.log_check(s)
            return

        log.info("СИГНАЛ %s %s @ %.4f | vol_ratio=%.2f уровень=%s(%s) dist=%.2f%% cross=%s/%s импульс=%s 4h=%s",
                 s.direction.upper(), symbol, s.close, s.vol_ratio,
                 s.level_price, s.level_kind, (s.level_dist_pct or 0) * 100,
                 s.cross_dir, s.cross_age, s.hist_impulse, s.trend_4h)
        if cfg.NOTIFY_SIGNALS:
            self.notify.send(notifier.fmt_signal(s))

        if symbol in cfg.WATCH_ONLY_SYMBOLS:
            # только наблюдение: считаем план для журнала, но не торгуем никогда
            inst = self.executor.instruments[symbol]
            plan, _ = risk.build_plan(symbol, s.direction, s.close, s.level_price,
                                      cfg.FALLBACK_BALANCE_USDT,
                                      inst["qty_step"], inst["min_qty"])
            if plan is not None:
                log.info("[WATCH-ONLY] %s %s qty=%s SL=%s TP=%s",
                         plan.side, symbol, plan.qty, plan.stop_loss, plan.take_profit)
                if cfg.NOTIFY_SIGNALS:
                    self.notify.send(notifier.fmt_plan(plan, "watch_only"))
                self.journal.log_check(s, skip_reason="watch_only",
                                       qty=plan.qty, entry=plan.entry,
                                       sl=plan.stop_loss, tp=plan.take_profit)
            else:
                self.journal.log_check(s, skip_reason="watch_only")
            return

        allowed, deny = self.risk.can_open(symbol, utcnow())
        if not allowed:
            log.info("сделка не открыта: %s", deny)
            if cfg.NOTIFY_SIGNALS:
                self.notify.send(notifier.fmt_skip(s, deny))
            self.journal.log_check(s, skip_reason=deny)
            return

        balance = cfg.FALLBACK_BALANCE_USDT
        if self.has_keys:
            try:
                balance = self.executor.balance_usdt()
            except Exception:
                log.exception("не удалось получить баланс — использую FALLBACK")

        inst = self.executor.instruments[symbol]
        plan, deny = risk.build_plan(symbol, s.direction, s.close, s.level_price,
                                     balance, inst["qty_step"], inst["min_qty"])
        if plan is None:
            log.info("сделка не открыта: %s", deny)
            if cfg.NOTIFY_SIGNALS:
                self.notify.send(notifier.fmt_skip(s, deny))
            self.journal.log_check(s, skip_reason=deny)
            return

        if cfg.DRY_RUN:
            log.info("[DRY-RUN] %s %s qty=%s SL=%s TP=%s (риск %.2f USDT)",
                     plan.side, symbol, plan.qty, plan.stop_loss, plan.take_profit, plan.risk_usdt)
            if cfg.NOTIFY_SIGNALS:
                self.notify.send(notifier.fmt_plan(plan, "dry_run"))
            self.journal.log_check(s, trade_opened=False, skip_reason="dry_run",
                                   qty=plan.qty, entry=plan.entry,
                                   sl=plan.stop_loss, tp=plan.take_profit)
            return

        order_id = self.executor.place_market(plan)
        opened = order_id is not None
        if cfg.NOTIFY_TRADES:
            self.notify.send(notifier.fmt_plan(plan, "opened") if opened
                             else f"⚠️ Ошибка API при выставлении ордера {plan.side} {symbol} — см. logs/errors.log")
        if opened:
            self.tracked[symbol] = {
                "side": plan.side, "qty": plan.qty, "entry": plan.entry,
                "sl": plan.stop_loss, "tp": plan.take_profit, "opened_ts": utcnow(),
            }
            self.risk.open_symbols.add(symbol)
        self.journal.log_check(s, trade_opened=opened,
                               skip_reason="" if opened else "ошибка API при выставлении ордера",
                               qty=plan.qty, entry=plan.entry,
                               sl=plan.stop_loss, tp=plan.take_profit)

    # ---------- циклы ----------

    def _load_instruments_and_prune(self) -> None:
        """Убирает из работы символы, которых нет на бирже (например, ещё не залистены)."""
        missing = self.executor.load_instruments()
        for sym in missing:
            for lst in (cfg.SYMBOLS, cfg.WATCH_ONLY_SYMBOLS, cfg.ALL_SYMBOLS):
                if sym in lst:
                    lst.remove(sym)
        if missing:
            msg = f"⚠️ Символы недоступны на бирже и исключены: {', '.join(missing)}"
            log.warning(msg)
            self.notify.send(msg)
        if not cfg.ALL_SYMBOLS:
            raise RuntimeError("ни один символ не доступен — проверьте сеть/гео-блок и список SYMBOLS")

    def run_once(self) -> None:
        """Разовый прогон по REST-истории: проверка пайплайна без ожидания свечей."""
        self._load_instruments_and_prune()
        self.market.load_history()
        self.sync_positions()
        for symbol in cfg.ALL_SYMBOLS:
            self.on_closed_bar(symbol)
        log.info("разовый прогон завершён, журнал: %s", self.journal.signals_path)

    def run(self) -> None:
        mode = "DRY-RUN (только сигналы)" if cfg.DRY_RUN else "ДЕМО-ТОРГОВЛЯ"
        log.info("старт бота: %s | %s | ТФ %sm/%sm", mode, cfg.SYMBOLS, cfg.SIGNAL_TF, cfg.TREND_TF)
        self._load_instruments_and_prune()
        if cfg.NOTIFY_TRADES:
            watch = f"\nWatch-only: {', '.join(cfg.WATCH_ONLY_SYMBOLS)}" if cfg.WATCH_ONLY_SYMBOLS else ""
            self.notify.send(f"🤖 Бот запущен: {mode}\n{', '.join(cfg.SYMBOLS)} | ТФ {cfg.SIGNAL_TF}m/{cfg.TREND_TF}m{watch}")
        self.market.load_history()
        self.sync_positions()
        self.market.start_ws()

        while True:
            try:
                symbol, interval = self.market.closed_bars.get(timeout=5)
                if interval == cfg.SIGNAL_TF:
                    log.info("закрылась свеча %sm %s", interval, symbol)
                    self.on_closed_bar(symbol)
            except queue.Empty:
                pass
            except Exception:
                log.exception("ошибка в главном цикле")  # не падаем

            now = time.time()
            if now - self._last_sync > cfg.POSITION_SYNC_SEC:
                self._last_sync = now
                self.sync_positions()
                self.risk.maybe_reset_day()
            if not self.market.ws_alive():
                try:
                    self.market.restart_ws()
                except Exception:
                    log.exception("реконнект WS не удался, попробую позже")
                    time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bybit demo trading bot")
    parser.add_argument("--once", action="store_true",
                        help="разовый прогон по истории и выход")
    args = parser.parse_args()

    setup_logging()
    pd.set_option("display.width", 160)
    bot = Bot()
    try:
        if args.once:
            bot.run_once()
        else:
            bot.run()
    except KeyboardInterrupt:
        log.info("остановка по Ctrl+C")
    except Exception as e:
        if "403" in str(e) or "usa" in str(e).lower() or "country" in str(e).lower():
            log.error("Bybit недоступен с этого IP (гео-блок CloudFront). "
                      "Нужен VPN не из заблокированного региона. Ошибка: %s", e)
        else:
            log.exception("фатальная ошибка при запуске")
        if cfg.NOTIFY_TRADES:
            bot.notify.send(f"💀 Бот остановлен из-за ошибки: {e}")
            time.sleep(3)  # даём потоку уведомлений отправить сообщение
        raise SystemExit(1)


if __name__ == "__main__":
    main()

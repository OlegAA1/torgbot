# torgbot — торговый бот на демо-счёте Bybit

Торгует BTC / ETH / SOL / XRP (USDT-перпетуалы, `category=linear`) по сигналам:
Дополнительно наблюдает TradFi-перпетуалы (NVDA, TSLA, AAPL, GOOGL, META, AMZN,
MSFT) в режиме watch-only (`WATCH_ONLY_SYMBOLS` в config: сигналы и журнал есть,
торговли нет никогда). Символы, которых нет на бирже, исключаются на старте
с предупреждением в лог и Telegram.
аномальный объём + подход к уровню S/R + крест MACD(12,26,9) / импульс гистограммы,
с фильтром тренда по EMA(50) на 4h. Только демо-среда Bybit, реальные деньги не используются.

## Установка

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # вписать API_KEY / API_SECRET демо-среды
```

Ключи создаются внутри Demo Trading на bybit.com (Profile → API → Create New Key),
права — только **Read + Trade**. Ключи живут только в `.env` (файл в `.gitignore`).

**Telegram-уведомления** (необязательно): создать бота у @BotFather, узнать свой
chat_id у @userinfobot, вписать `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` в `.env`
и один раз написать своему боту любое сообщение (иначе Telegram не даст ему писать вам).
Что присылать — флаги `NOTIFY_SIGNALS` / `NOTIFY_TRADES` в `bot/config.py`.

⚠️ **Bybit блокирует ряд стран/IP на уровне CloudFront** (в т.ч. IP этой машины на момент
сборки). Если REST отвечает `403 ... blocked access from your country` — нужен VPN.

## Запуск

```bash
.venv/bin/python -m tools.check_indicators BTCUSDT  # шаг 1: сверка индикаторов с TradingView
.venv/bin/python -m bot.main --once                 # разовый прогон пайплайна по истории
.venv/bin/python -m bot.main                        # рабочий режим (WebSocket)
```

Порядок ввода в работу (не нарушать):
1. `tools.check_indicators` — сверить MACD/EMA с графиком TradingView/Bybit (UTC!).
2. **Dry-run 2–3 дня** (`DRY_RUN = True` в `bot/config.py`, по умолчанию): бот только
   печатает сигналы и пишет журнал.
3. Демо-торговля: `DRY_RUN = False`, наблюдать неделю.
4. Анализ `journal/signals.csv` и `journal/trades.csv` → правка порогов в config → повтор.

## Структура

```
bot/config.py      все параметры (пороги, ТФ, риск) — менять только здесь
bot/data.py        REST-история + WebSocket kline (только закрытые свечи, confirm=true)
bot/indicators.py  vol_ratio, EMA, MACD, гистограмма, фрактальные уровни S/R
bot/signals.py     объединение условий в сигнал long/short
bot/risk.py        размер позиции (1% риска), SL/TP (R:R 1:2), лимиты, kill switch, кулдаун
bot/executor.py    ордера через API v5 (demo=True), округление qty по qtyStep
bot/journal.py     journal/signals.csv (каждая проверка) + journal/trades.csv (итоги сделок)
bot/notifier.py    Telegram-уведомления (сигналы, сделки, kill switch, старт/стоп)
bot/main.py        главный цикл
tools/check_indicators.py  сверка индикаторов + самопроверка MACD
```

## Деплой на VPS (Ubuntu)

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/OlegAA1/torgbot.git && cd torgbot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # ключи демо-API + Telegram
chmod 600 .env
.venv/bin/python -m bot.main --once    # проверка пайплайна
bash deploy/install.sh                 # systemd-сервис с автозапуском
```

Логи: `journalctl -u torgbot -f`. Перед установкой проверить, что Bybit не
гео-блокирует IP сервера: `curl https://api-demo.bybit.com/v5/market/time`
должен вернуть JSON, а не ошибку CloudFront.

## Жёсткие правила (зашиты в код)

- риск 1% баланса на сделку, SL сразу в ордере, TP = 2R;
- ≤1 позиции на символ, ≤3 всего; никакого усреднения;
- 3 стопа подряд → kill switch до конца дня UTC;
- кулдаун 4 свечи после закрытия сделки по символу;
- бот не двигает стопы после входа;
- сигналы только по закрытым свечам; время везде UTC.

Состояние (kill switch, кулдауны) хранится в `state.json`; при рестарте открытые позиции
подхватываются с биржи через API. Ошибки — в `logs/errors.log`.

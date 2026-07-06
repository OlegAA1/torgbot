"""Все параметры бота в одном месте. Никаких магических чисел в остальном коде."""

# --- Режим работы ---
DRY_RUN = True          # True: только сигналы и журнал, ордера не выставляются
DEMO = True             # демо-среда Bybit (api-demo.bybit.com). НЕ выключать в этом ТЗ.

# --- Инструменты и таймфреймы ---
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
# Watch-only: сигналы, журнал и уведомления, но торговля НИКОГДА не ведётся,
# даже при DRY_RUN=False. Эксперимент с TradFi-перпетуалами (акции США, 24/7):
# у них сессионный ритм объёма (пик на открытии США, тишина ночью/в выходные),
# поэтому vol_ratio может искажаться — сначала копим статистику в журнале.
WATCH_ONLY_SYMBOLS = ["NVDAUSDT", "TSLAUSDT", "AAPLUSDT", "GOOGLUSDT",
                      "METAUSDT", "AMZNUSDT", "MSFTUSDT"]
ALL_SYMBOLS = SYMBOLS + WATCH_ONLY_SYMBOLS
CATEGORY = "linear"     # USDT-перпетуалы
SIGNAL_TF = "15"        # рабочий ТФ (интервал Bybit, минуты)
TREND_TF = "240"        # ТФ подтверждения тренда (4h)
HISTORY_LIMIT = 500     # свечей истории на каждый ТФ при инициализации

# --- Аномальный объём ---
VOL_SMA_PERIOD = 20
VOL_RATIO_THRESHOLD = 2.0   # volume / SMA(volume, 20) >= порога

# --- MACD ---
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD_CROSS_MAX_AGE = 3      # крест не старше N закрытых свечей
HIST_IMPULSE_BARS = 2       # гистограмма растёт (для long) N свечей подряд
MACD_NEAR_ZERO_PCT = 0.001  # линия MACD не глубже 0.1% цены по «чужую» сторону нуля

# --- Уровни поддержки/сопротивления ---
SR_LOOKBACK = 200           # свечей для поиска фрактальных экстремумов (100–200)
SR_FRACTAL_WING = 2         # пивот: экстремум выше/ниже N соседей слева и справа
SR_CLUSTER_PCT = 0.003      # уровни ближе 0.3% сливаются в один
SR_PROXIMITY_PCT = 0.005    # «подход к уровню» = цена в пределах 0.5%
SR_LEVEL_MAX_AGE_BARS = 96  # фрактал старше N закрытых свечей не участвует (96 x 15m = сутки)
BREAKOUT_MAX_DIST_PCT = 0.003  # пробой валиден, если закрытие не дальше 0.3% от уровня (иначе поздно)

# --- Фильтр старшего ТФ ---
USE_TREND_FILTER = True     # False — отключить фильтр 4h
TREND_EMA_PERIOD = 50       # цена выше EMA(50) 4h -> только long, ниже -> только short

# --- Локальный трендовый фильтр 15m ---
USE_LOCAL_TREND_FILTER = True
LOCAL_EMA_FAST = 20         # long: EMA(fast) > EMA(slow) на 15m, short — наоборот
LOCAL_EMA_SLOW = 50

# --- Дедупликация сигналов ---
SIGNAL_DEDUP_BARS = 8        # не повторять сигнал (символ, уровень, направление) N свечей
SIGNAL_DEDUP_RESET_PCT = 0.01  # ...или пока цена не отойдёт от уровня на 1%

# --- Риск-менеджмент ---
RISK_PER_TRADE = 0.01       # 1% демо-баланса на сделку
SL_BUFFER_PCT = 0.005       # стоп за уровнем: 0.5% ниже поддержки (выше сопротивления)
RISK_REWARD = 2.0           # тейк = R:R 1:2 (потолок; см. структурный TP ниже)
USE_STRUCT_TP = True        # TP не дальше ближайшего препятствия (старого пивота) по ходу сделки
STRUCT_TP_BUFFER_PCT = 0.002  # тейк ставится не доходя 0.2% до препятствия
MIN_TP_RR = 1.0             # если структурный TP ближе N*R — сигнал пропускается (не окупает риск)
ATR_PERIOD = 14             # ATR на рабочем ТФ (для минимальной дистанции стопа)
MIN_SL_ATR_MULT = 0.5       # минимальная дистанция SL = max(0.5*ATR, MIN_SL_PCT*цена)
MIN_SL_PCT = 0.003          # ...но не меньше 0.3% от цены; более узкий стоп отодвигается
MAX_POS_NOTIONAL_PCT = 0.5  # notional одной позиции <= 50% баланса
MAX_TOTAL_NOTIONAL_PCT = 1.5  # суммарный notional всех открытых позиций <= 150% баланса
MAX_DIRECTION_RISK = 0.015  # суммарный риск однонаправленных позиций <= 1.5% (корреляция мажоров)
MAX_POS_PER_SYMBOL = 1
MAX_POS_TOTAL = 3
MAX_CONSEC_STOPS_PER_DAY = 3  # kill switch: N стопов подряд -> стоп торговли до конца дня (UTC)
COOLDOWN_BARS = 4           # пауза после закрытия сделки по символу, в свечах SIGNAL_TF
FALLBACK_BALANCE_USDT = 10000.0  # для dry-run без ключей (расчёт размера позиции)

# --- Telegram-уведомления (TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID — в .env) ---
NOTIFY_SIGNALS = True   # сигналы, dry-run планы, пропуски по риск-лимитам
NOTIFY_TRADES = True    # открытие/закрытие сделок, kill switch, старт/остановка бота
NOTIFY_TZ_OFFSET_HOURS = 3   # часовой пояс времени в уведомлениях (МСК = UTC+3)
NOTIFY_TZ_LABEL = "МСК"      # подпись пояса в сообщениях (журнал остаётся в UTC)

# --- Служебное ---
# Публичные kline-стримы в демо-среде Bybit совпадают с mainnet
# (stream-demo гарантирует только приватные каналы), поэтому публичный WS
# по умолчанию идёт на wss://stream.bybit.com. Торговый REST остаётся демо.
WS_PUBLIC_DEMO = False
POSITION_SYNC_SEC = 60      # период сверки открытых позиций с биржей
WS_STALE_SEC = 900          # нет сообщений WS дольше N секунд -> реконнект
LOG_DIR = "logs"
JOURNAL_DIR = "journal"
STATE_FILE = "state.json"   # kill switch / кулдауны, переживает перезапуск
PAPER_FILE = "paper_positions.json"  # виртуальные позиции dry-run, переживают перезапуск

"""Все параметры бота в одном месте. Никаких магических чисел в остальном коде."""

# --- Режим работы ---
DRY_RUN = True          # True: только сигналы и журнал, ордера не выставляются
DEMO = True             # демо-среда Bybit (api-demo.bybit.com). НЕ выключать в этом ТЗ.

# --- Инструменты и таймфреймы ---
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
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
HIST_IMPULSE_BARS = 3       # |hist| растёт N свечей подряд
HIST_AVG_PERIOD = 20        # период среднего |hist|
HIST_IMPULSE_MULT = 1.5     # |hist| > MULT * средний |hist|

# --- Уровни поддержки/сопротивления ---
SR_LOOKBACK = 200           # свечей для поиска фрактальных экстремумов (100–200)
SR_FRACTAL_WING = 2         # пивот: экстремум выше/ниже N соседей слева и справа
SR_CLUSTER_PCT = 0.003      # уровни ближе 0.3% сливаются в один
SR_PROXIMITY_PCT = 0.005    # «подход к уровню» = цена в пределах 0.5%

# --- Фильтр старшего ТФ ---
USE_TREND_FILTER = True     # False — отключить фильтр 4h
TREND_EMA_PERIOD = 50       # цена выше EMA(50) 4h -> только long, ниже -> только short

# --- Риск-менеджмент ---
RISK_PER_TRADE = 0.01       # 1% демо-баланса на сделку
SL_BUFFER_PCT = 0.005       # стоп за уровнем: 0.5% ниже поддержки (выше сопротивления)
RISK_REWARD = 2.0           # тейк = R:R 1:2
MAX_POS_PER_SYMBOL = 1
MAX_POS_TOTAL = 3
MAX_CONSEC_STOPS_PER_DAY = 3  # kill switch: N стопов подряд -> стоп торговли до конца дня (UTC)
COOLDOWN_BARS = 4           # пауза после закрытия сделки по символу, в свечах SIGNAL_TF
FALLBACK_BALANCE_USDT = 10000.0  # для dry-run без ключей (расчёт размера позиции)

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

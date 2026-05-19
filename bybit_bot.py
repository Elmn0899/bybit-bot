#!/usr/bin/env python3
"""
Bybit Pattern Scanner Bot
Мониторит все фьючерсы на Bybit (5м TF)
Ищет паттерн истощения: 3-4 маленькие свечи подряд (в 2-3 раза меньше предыдущих)
Отправляет уведомление в Telegram
"""

import requests
import time
import logging
from datetime import datetime

# ============================================================
# НАСТРОЙКИ
# ============================================================
TELEGRAM_TOKEN = "8032574245:AAHkuQCLeExNB5WSdxcsIXxrjirjAXL5IHU"
CHAT_ID = "732160810"
CHECK_INTERVAL = 60        # Проверка каждые 60 секунд
TIMEFRAME = "5"            # 5 минут
CANDLES_TO_FETCH = 20      # Сколько свечей брать для анализа

# Параметры паттерна
SMALL_CANDLES_COUNT = 3    # Минимум маленьких свечей подряд
SIZE_RATIO = 2.0           # Во сколько раз меньше (2-3x)

# Защита от спама — не слать одну монету чаще чем раз в N минут
COOLDOWN_MINUTES = 30
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Словарь последних уведомлений {symbol: timestamp}
last_alerts = {}


def send_telegram(message: str):
    """Отправить сообщение в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if r.status_code == 200:
            log.info(f"✅ Telegram отправлен")
        else:
            log.error(f"Telegram ошибка: {r.text}")
    except Exception as e:
        log.error(f"Telegram exception: {e}")


def get_all_symbols() -> list:
    """Получить все линейные фьючерсы с Bybit"""
    url = "https://api.bybit.com/v5/market/instruments-info"
    symbols = []
    cursor = None

    try:
        while True:
            params = {
                "category": "linear",
                "status": "Trading",
                "limit": 1000
            }
            if cursor:
                params["cursor"] = cursor

            r = requests.get(url, params=params, timeout=15)
            text = r.text
            data = r.json()

            if data.get("retCode") == 0:
                batch = [
                    item["symbol"] for item in data["result"]["list"]
                    if item["symbol"].endswith("USDT")
                ]
                symbols.extend(batch)
                cursor = data["result"].get("nextPageCursor")
                if not cursor:
                    break
            else:
                log.error(f"Bybit API ошибка: {data}")
                break

        log.info(f"Найдено монет: {len(symbols)}")
        return symbols

    except Exception as e:
        log.error(f"Ошибка получения монет: {e}")
    return []


def get_candles(symbol: str) -> list:
    """
    Получить последние свечи по монете
    Возвращает список: [{open, high, low, close, body_size}, ...]
    Отсортированы от старых к новым
    """
    url = "https://api.bybit.com/v5/market/kline"
    try:
        r = requests.get(url, params={
            "category": "linear",
            "symbol": symbol,
            "interval": TIMEFRAME,
            "limit": CANDLES_TO_FETCH
        }, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            candles = []
            for c in reversed(data["result"]["list"]):  # от старых к новым
                o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                body = abs(cl - o)
                full_range = h - l
                candles.append({
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": cl,
                    "body": body,
                    "range": full_range
                })
            return candles
    except Exception as e:
        log.error(f"Ошибка свечей {symbol}: {e}")
    return []


def detect_exhaustion(candles: list) -> bool:
    """
    Определяет паттерн истощения:
    - Последние 3-4 свечи маленькие (тело в 2-3 раза меньше предыдущих)
    - Предыдущие свечи были нормального/большого размера
    """
    if len(candles) < SMALL_CANDLES_COUNT + 3:
        return False

    # Берём последние N свечей как "маленькие" кандидаты
    recent = candles[-(SMALL_CANDLES_COUNT):]
    # Предыдущие свечи до паттерна (для сравнения размера)
    before = candles[-(SMALL_CANDLES_COUNT + 5):-(SMALL_CANDLES_COUNT)]

    if not before:
        return False

    # Средний размер свечей ДО паттерна (используем range = high-low)
    avg_before = sum(c["range"] for c in before) / len(before)

    if avg_before == 0:
        return False

    # Проверяем: все последние свечи маленькие?
    small_count = 0
    for c in recent:
        if c["range"] > 0 and (avg_before / c["range"]) >= SIZE_RATIO:
            small_count += 1

    # Сработало если минимум SMALL_CANDLES_COUNT свечей маленькие
    return small_count >= SMALL_CANDLES_COUNT


def is_in_cooldown(symbol: str) -> bool:
    """Проверить не отправляли ли мы уже уведомление недавно"""
    if symbol not in last_alerts:
        return False
    elapsed = (datetime.now() - last_alerts[symbol]).total_seconds() / 60
    return elapsed < COOLDOWN_MINUTES


def format_alert(symbol: str, candles: list) -> str:
    """Форматировать сообщение для Telegram"""
    last = candles[-1]
    recent = candles[-SMALL_CANDLES_COUNT:]
    before = candles[-(SMALL_CANDLES_COUNT + 5):-(SMALL_CANDLES_COUNT)]

    avg_before = sum(c["range"] for c in before) / len(before) if before else 1
    avg_recent = sum(c["range"] for c in recent) / len(recent) if recent else 1
    ratio = avg_before / avg_recent if avg_recent > 0 else 0

    return (
        f"⚠️ <b>ПАТТЕРН ИСТОЩЕНИЯ</b> ⚠️\n\n"
        f"🔴 <b>Монета:</b> {symbol}\n"
        f"📊 <b>Таймфрейм:</b> 5 минут\n"
        f"💰 <b>Цена:</b> {last['close']}\n"
        f"📉 <b>Свечи стали меньше в:</b> {ratio:.1f}x\n"
        f"🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}\n\n"
        f"⚡️ Возможный шорт!"
    )


def scan_all_symbols(symbols: list):
    """Один проход по всем монетам"""
    alerts_sent = 0
    checked = 0

    for symbol in symbols:
        if is_in_cooldown(symbol):
            continue

        candles = get_candles(symbol)
        if not candles:
            continue

        checked += 1

        if detect_exhaustion(candles):
            message = format_alert(symbol, candles)
            send_telegram(message)
            last_alerts[symbol] = datetime.now()
            alerts_sent += 1
            log.info(f"🔴 ПАТТЕРН: {symbol}")
            time.sleep(0.5)  # небольшая пауза между отправками

        # Bybit rate limit: не более 10 запросов в секунду
        time.sleep(0.12)

    log.info(f"Проверено: {checked} монет, сигналов: {alerts_sent}")


def main():
    log.info("=" * 50)
    log.info("🤖 Bybit Pattern Bot запущен")
    log.info(f"Таймфрейм: {TIMEFRAME}м | Cooldown: {COOLDOWN_MINUTES}м")
    log.info("=" * 50)

    # Тестовое сообщение
    send_telegram(
        "🤖 <b>Bybit Scanner запущен!</b>\n\n"
        "Мониторю все USDT фьючерсы на 5м TF\n"
        "Ищу паттерн истощения для шорта 🔴"
    )

    symbols = []

    while True:
        try:
            # Обновляем список монет каждые 10 минут
            if not symbols:
                symbols = get_all_symbols()
                if not symbols:
                    log.error("Не удалось получить список монет, повтор через 60с")
                    time.sleep(60)
                    continue

            log.info(f"🔍 Сканирую {len(symbols)} монет...")
            scan_all_symbols(symbols)

            # Обновить список монет через 10 циклов
            if len(last_alerts) % 10 == 0:
                symbols = get_all_symbols() or symbols

            log.info(f"⏳ Ожидание {CHECK_INTERVAL}с до следующей проверки...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("Бот остановлен")
            break
        except Exception as e:
            log.error(f"Главная ошибка: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()

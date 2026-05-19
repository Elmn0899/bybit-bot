#!/usr/bin/env python3
import requests
import time
import logging
from datetime import datetime

TELEGRAM_TOKEN = "8032574245:AAHkuQCLeExNB5WSdxcsIXxrjirjAXL5IHU"
CHAT_ID = "732160810"
CHECK_INTERVAL = 60
TIMEFRAME = "5"
CANDLES_TO_FETCH = 40
COOLDOWN_MINUTES = 180  # 3 часа между сигналами по одной монете

HEADERS = {
    "User-Agent": "python-requests/2.31.0",
    "Accept": "application/json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# {symbol: timestamp}
last_alerts = {}

# Отслеживание результатов сигналов
# {symbol: {"time": datetime, "price": float, "checked_30": bool, "checked_60": bool}}
active_signals = {}


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if r.status_code == 200:
            log.info("Telegram OK")
        else:
            log.error(f"Telegram error: {r.status_code}")
    except Exception as e:
        log.error(f"Telegram exception: {e}")


def get_current_price(symbol):
    url = "https://api.bybit.com/v5/market/tickers"
    try:
        r = requests.get(url, headers=HEADERS, params={
            "category": "linear",
            "symbol": symbol
        }, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("retCode") == 0:
                return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        pass
    return None


def check_signal_results():
    """Проверяем результаты прошлых сигналов и отправляем отчёт"""
    now = datetime.now()
    to_remove = []

    for symbol, info in active_signals.items():
        elapsed_min = (now - info["time"]).total_seconds() / 60

        # Отчёт через 30 минут
        if elapsed_min >= 30 and not info.get("checked_30"):
            current = get_current_price(symbol)
            if current:
                signal_price = info["price"]
                change_pct = (signal_price - current) / signal_price * 100
                direction = "📉 упала" if change_pct > 0 else "📈 выросла"
                emoji = "✅" if change_pct > 0 else "❌"

                send_telegram(
                    f"{emoji} <b>ОТЧЁТ 30 минут</b>\n\n"
                    f"📌 <b>{symbol}</b>\n"
                    f"💰 Цена сигнала: {signal_price}\n"
                    f"💰 Цена сейчас: {current}\n"
                    f"📊 Цена {direction} на: <b>{abs(change_pct):.2f}%</b>\n"
                    f"⏰ Прошло: 30 минут"
                )
                active_signals[symbol]["checked_30"] = True
                time.sleep(0.5)

        # Отчёт через 60 минут
        if elapsed_min >= 60 and not info.get("checked_60"):
            current = get_current_price(symbol)
            if current:
                signal_price = info["price"]
                change_pct = (signal_price - current) / signal_price * 100
                direction = "📉 упала" if change_pct > 0 else "📈 выросла"
                emoji = "✅" if change_pct > 0 else "❌"

                send_telegram(
                    f"{emoji} <b>ОТЧЁТ 1 час</b>\n\n"
                    f"📌 <b>{symbol}</b>\n"
                    f"💰 Цена сигнала: {signal_price}\n"
                    f"💰 Цена сейчас: {current}\n"
                    f"📊 Цена {direction} на: <b>{abs(change_pct):.2f}%</b>\n"
                    f"⏰ Прошло: 1 час"
                )
                active_signals[symbol]["checked_60"] = True
                time.sleep(0.5)

        # Удаляем после 2 часов
        if elapsed_min >= 120:
            to_remove.append(symbol)

    for s in to_remove:
        active_signals.pop(s, None)


def get_all_symbols():
    url = "https://api.bybit.com/v5/market/instruments-info"
    try:
        r = requests.get(url, headers=HEADERS, params={
            "category": "linear",
            "status": "Trading",
            "limit": 1000
        }, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get("retCode") == 0:
            symbols = [
                x["symbol"] for x in data["result"]["list"]
                if x["symbol"].endswith("USDT")
            ]
            log.info(f"Found {len(symbols)} symbols")
            return symbols
    except Exception as e:
        log.error(f"get_symbols error: {e}")
    return []


def get_candles(symbol):
    url = "https://api.bybit.com/v5/market/kline"
    try:
        r = requests.get(url, headers=HEADERS, params={
            "category": "linear",
            "symbol": symbol,
            "interval": TIMEFRAME,
            "limit": CANDLES_TO_FETCH
        }, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get("retCode") == 0:
            candles = []
            for c in reversed(data["result"]["list"]):
                o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                vol = float(c[5])
                candles.append({
                    "open": o, "high": h, "low": l, "close": cl,
                    "range": h - l,
                    "body": abs(cl - o),
                    "volume": vol
                })
            return candles
    except Exception:
        pass
    return []


def detect_pattern(candles):
    """
    МАКСИМАЛЬНО СТРОГИЙ паттерн:
    1. Явный рост минимум 3% до паттерна
    2. Все 4 свечи закрываются строго ниже
    3. Каждая свеча меньше предыдущей (постепенное затухание)
    4. Свечи уменьшились в 3x по сравнению с периодом роста
    5. Объём упал в 3x
    6. Цена ниже максимума минимум на 1%
    7. Последняя свеча очень маленькая (меньше 30% от среднего)
    """
    if len(candles) < 20:
        return False

    last4 = candles[-4:]
    before = candles[-18:-4]

    # --- 1. Явный рост минимум 3% ---
    price_low = min(c["low"] for c in before[:6])
    price_peak = max(c["high"] for c in before)
    growth = (price_peak - price_low) / price_low if price_low > 0 else 0
    if growth < 0.03:
        return False

    # --- 2. ВСЕ 4 свечи строго ниже ---
    closes = [c["close"] for c in last4]
    if not all(closes[i] < closes[i-1] for i in range(1, len(closes))):
        return False

    # --- 3. Каждая свеча меньше предыдущей (затухание) ---
    ranges = [c["range"] for c in last4]
    if not all(ranges[i] < ranges[i-1] for i in range(1, len(ranges))):
        return False

    # --- 4. Свечи уменьшились в 3x ---
    avg_range_before = sum(c["range"] for c in before) / len(before)
    avg_range_last4 = sum(ranges) / len(ranges)
    if avg_range_before == 0 or avg_range_last4 == 0:
        return False
    if avg_range_before / avg_range_last4 < 3.0:
        return False

    # --- 5. Объём упал в 3x ---
    avg_vol_before = sum(c["volume"] for c in before) / len(before)
    avg_vol_last4 = sum(c["volume"] for c in last4) / len(last4)
    if avg_vol_before == 0 or avg_vol_last4 == 0:
        return False
    if avg_vol_before / avg_vol_last4 < 3.0:
        return False

    # --- 6. Цена ниже максимума минимум на 1% ---
    current_price = candles[-1]["close"]
    local_max = max(c["high"] for c in candles[-18:])
    below_max = (local_max - current_price) / local_max if local_max > 0 else 0
    if below_max < 0.01:
        return False

    # --- 7. Последняя свеча очень маленькая ---
    last_range = candles[-1]["range"]
    if avg_range_before == 0:
        return False
    if last_range / avg_range_before > 0.25:  # должна быть меньше 25% от среднего
        return False

    return True


def is_in_cooldown(symbol):
    if symbol not in last_alerts:
        return False
    elapsed = (datetime.now() - last_alerts[symbol]).total_seconds() / 60
    return elapsed < COOLDOWN_MINUTES


def format_alert(symbol, candles):
    last = candles[-1]
    last4 = candles[-4:]
    before = candles[-18:-4]

    avg_range_before = sum(c["range"] for c in before) / len(before) if before else 1
    avg_range_last = sum(c["range"] for c in last4) / len(last4) if last4 else 1
    candle_ratio = avg_range_before / avg_range_last if avg_range_last > 0 else 0

    avg_vol_before = sum(c["volume"] for c in before) / len(before) if before else 1
    avg_vol_last = sum(c["volume"] for c in last4) / len(last4) if last4 else 1
    vol_ratio = avg_vol_before / avg_vol_last if avg_vol_last > 0 else 0

    local_max = max(c["high"] for c in candles[-18:])
    drop_pct = (local_max - last["close"]) / local_max * 100

    price_low = min(c["low"] for c in before[:6])
    growth_pct = (local_max - price_low) / price_low * 100

    return (
        f"🔴 <b>ШОРТ СИГНАЛ</b>\n\n"
        f"📌 <b>{symbol}</b>\n"
        f"💰 Цена входа: <b>{last['close']}</b>\n\n"
        f"📈 Рост до паттерна: {growth_pct:.1f}%\n"
        f"📉 Откат от максимума: {drop_pct:.2f}%\n"
        f"🕯 Свечи уменьшились в: {candle_ratio:.1f}x\n"
        f"📊 Объём упал в: {vol_ratio:.1f}x\n\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
        f"<i>Отчёт придёт через 30 мин и 1 час</i>"
    )


def scan_all_symbols(symbols):
    alerts_sent = 0
    for symbol in symbols:
        if is_in_cooldown(symbol):
            continue
        candles = get_candles(symbol)
        if not candles:
            continue
        if detect_pattern(candles):
            msg = format_alert(symbol, candles)
            send_telegram(msg)
            last_alerts[symbol] = datetime.now()
            active_signals[symbol] = {
                "time": datetime.now(),
                "price": candles[-1]["close"],
                "checked_30": False,
                "checked_60": False
            }
            alerts_sent += 1
            log.info(f"SIGNAL: {symbol}")
            time.sleep(0.5)
        time.sleep(0.1)
    log.info(f"Scan done, signals: {alerts_sent}")


def main():
    log.info("Bot starting...")
    send_telegram(
        "<b>Bybit Scanner v4 запущен!</b>\n\n"
        "Строгий паттерн: рост 3%+ → дуга вниз → затухание → объём x3\n"
        "Отчёт по каждому сигналу через 30 мин и 1 час 📊"
    )

    symbols = []
    cycle = 0

    while True:
        try:
            # Проверяем результаты прошлых сигналов
            check_signal_results()

            if not symbols or cycle % 10 == 0:
                new_symbols = get_all_symbols()
                if new_symbols:
                    symbols = new_symbols
                elif not symbols:
                    log.error("No symbols, retry in 60s")
                    time.sleep(60)
                    continue

            log.info(f"Scanning {len(symbols)} symbols...")
            scan_all_symbols(symbols)
            cycle += 1
            log.info(f"Waiting {CHECK_INTERVAL}s...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("Stopped")
            break
        except Exception as e:
            log.error(f"Main error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import requests
import time
import logging
from datetime import datetime

TELEGRAM_TOKEN = "8032574245:AAHkuQCLeExNB5WSdxcsIXxrjirjAXL5IHU"
CHAT_ID = "732160810"
CHECK_INTERVAL = 60
TIMEFRAME = "5"
CANDLES_TO_FETCH = 30
COOLDOWN_MINUTES = 60

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
last_alerts = {}


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
    Паттерн истощения:
    1. Был рост до паттерна
    2. Последние 4 свечи: цена загибается вниз
    3. Свечи становятся меньше (затухание)
    4. Объём падает вместе со свечами
    5. Цена ниже локального максимума
    """
    if len(candles) < 15:
        return False

    last4 = candles[-4:]
    before = candles[-14:-4]

    # --- 1. До паттерна был рост ---
    price_low = min(c["low"] for c in before[:5])
    price_peak = max(c["high"] for c in before)
    growth = (price_peak - price_low) / price_low if price_low > 0 else 0
    if growth < 0.008:  # минимум 0.8% рост
        return False

    # --- 2. Изгиб вниз: последние 4 свечи закрываются ниже ---
    closes = [c["close"] for c in last4]
    descending = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
    if descending < 2:  # хотя бы 2 из 3 переходов вниз
        return False

    # --- 3. Затухание свечей ---
    ranges = [c["range"] for c in last4]
    avg_range_before = sum(c["range"] for c in before) / len(before)
    avg_range_last4 = sum(ranges) / len(ranges)
    if avg_range_before == 0:
        return False
    candle_ratio = avg_range_before / avg_range_last4
    if candle_ratio < 1.5:  # свечи должны быть в 1.5x меньше
        return False

    # --- 4. Объём падает ---
    avg_vol_before = sum(c["volume"] for c in before) / len(before)
    avg_vol_last4 = sum(c["volume"] for c in last4) / len(last4)
    if avg_vol_before == 0:
        return False
    vol_ratio = avg_vol_before / avg_vol_last4
    if vol_ratio < 1.5:  # объём должен упасть в 1.5x
        return False

    # --- 5. Цена ниже локального максимума ---
    current_price = candles[-1]["close"]
    local_max = max(c["high"] for c in candles[-14:])
    below_max = (local_max - current_price) / local_max if local_max > 0 else 0
    if below_max < 0.003:
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
    before = candles[-14:-4]

    avg_range_before = sum(c["range"] for c in before) / len(before) if before else 1
    avg_range_last = sum(c["range"] for c in last4) / len(last4) if last4 else 1
    candle_ratio = avg_range_before / avg_range_last if avg_range_last > 0 else 0

    avg_vol_before = sum(c["volume"] for c in before) / len(before) if before else 1
    avg_vol_last = sum(c["volume"] for c in last4) / len(last4) if last4 else 1
    vol_ratio = avg_vol_before / avg_vol_last if avg_vol_last > 0 else 0

    local_max = max(c["high"] for c in candles[-14:])
    drop_pct = (local_max - last["close"]) / local_max * 100

    return (
        f"🔴 <b>ШОРТ СИГНАЛ</b>\n\n"
        f"📌 <b>{symbol}</b>\n"
        f"💰 Цена: <b>{last['close']}</b>\n"
        f"📉 Ниже максимума: {drop_pct:.2f}%\n"
        f"🕯 Свечи меньше в: {candle_ratio:.1f}x\n"
        f"📊 Объём упал в: {vol_ratio:.1f}x\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}\n\n"
        f"Дуга вниз + затухание + падение объёма"
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
            alerts_sent += 1
            log.info(f"SIGNAL: {symbol}")
            time.sleep(0.5)
        time.sleep(0.1)
    log.info(f"Scan done, signals: {alerts_sent}")


def main():
    log.info("Bot starting...")
    send_telegram(
        "<b>Bybit Scanner v3 запущен!</b>\n\n"
        "Паттерн: дуга вниз + затухание + падение объёма 🔴\n"
        "Таймфрейм: 5м"
    )

    symbols = []
    cycle = 0

    while True:
        try:
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

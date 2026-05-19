#!/usr/bin/env python3
import requests
import time
import logging
from datetime import datetime

TELEGRAM_TOKEN = "8032574245:AAHkuQCLeExNB5WSdxcsIXxrjirjAXL5IHU"
CHAT_ID = "732160810"
CHECK_INTERVAL = 60
TIMEFRAME = "5"
CANDLES_TO_FETCH = 20
SMALL_CANDLES_COUNT = 3
SIZE_RATIO = 2.0
COOLDOWN_MINUTES = 30

HEADERS = {
    "User-Agent": "python-requests/2.31.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
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
            log.error(f"Telegram error: {r.status_code} {r.text[:100]}")
    except Exception as e:
        log.error(f"Telegram exception: {e}")


def get_all_symbols():
    url = "https://api.bybit.com/v5/market/instruments-info"
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            params={"category": "linear", "status": "Trading", "limit": 1000},
            timeout=20
        )
        log.info(f"Symbols API status: {r.status_code}")
        if r.status_code != 200:
            log.error(f"Bad status: {r.text[:200]}")
            return []
        data = r.json()
        if data.get("retCode") == 0:
            symbols = [
                x["symbol"] for x in data["result"]["list"]
                if x["symbol"].endswith("USDT")
            ]
            log.info(f"Found {len(symbols)} symbols")
            return symbols
        else:
            log.error(f"API retCode error: {data.get('retMsg')}")
    except Exception as e:
        log.error(f"get_symbols error: {e}")
    return []


def get_candles(symbol):
    url = "https://api.bybit.com/v5/market/kline"
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": TIMEFRAME,
                "limit": CANDLES_TO_FETCH
            },
            timeout=10
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get("retCode") == 0:
            candles = []
            for c in reversed(data["result"]["list"]):
                o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                candles.append({
                    "open": o, "high": h, "low": l, "close": cl,
                    "range": h - l,
                    "body": abs(cl - o)
                })
            return candles
    except Exception as e:
        pass
    return []


def detect_exhaustion(candles):
    if len(candles) < SMALL_CANDLES_COUNT + 3:
        return False
    recent = candles[-SMALL_CANDLES_COUNT:]
    before = candles[-(SMALL_CANDLES_COUNT + 5):-SMALL_CANDLES_COUNT]
    if not before:
        return False
    avg_before = sum(c["range"] for c in before) / len(before)
    if avg_before == 0:
        return False
    small_count = sum(
        1 for c in recent
        if c["range"] > 0 and (avg_before / c["range"]) >= SIZE_RATIO
    )
    return small_count >= SMALL_CANDLES_COUNT


def is_in_cooldown(symbol):
    if symbol not in last_alerts:
        return False
    elapsed = (datetime.now() - last_alerts[symbol]).total_seconds() / 60
    return elapsed < COOLDOWN_MINUTES


def format_alert(symbol, candles):
    last = candles[-1]
    recent = candles[-SMALL_CANDLES_COUNT:]
    before = candles[-(SMALL_CANDLES_COUNT + 5):-SMALL_CANDLES_COUNT]
    avg_before = sum(c["range"] for c in before) / len(before) if before else 1
    avg_recent = sum(c["range"] for c in recent) / len(recent) if recent else 1
    ratio = avg_before / avg_recent if avg_recent > 0 else 0
    return (
        f"<b>СИГНАЛ ШОРТ</b>\n\n"
        f"Монета: <b>{symbol}</b>\n"
        f"Цена: {last['close']}\n"
        f"Свечи уменьшились в: {ratio:.1f}x\n"
        f"Время: {datetime.now().strftime('%H:%M:%S')}\n\n"
        f"Паттерн истощения на 5м TF"
    )


def scan_all_symbols(symbols):
    alerts_sent = 0
    for symbol in symbols:
        if is_in_cooldown(symbol):
            continue
        candles = get_candles(symbol)
        if not candles:
            continue
        if detect_exhaustion(candles):
            msg = format_alert(symbol, candles)
            send_telegram(msg)
            last_alerts[symbol] = datetime.now()
            alerts_sent += 1
            log.info(f"SIGNAL: {symbol}")
            time.sleep(0.5)
        time.sleep(0.12)
    log.info(f"Scan done, signals: {alerts_sent}")


def main():
    log.info("Bot starting...")
    send_telegram(
        "<b>Bybit Scanner запущен!</b>\n\n"
        "Мониторю все USDT фьючерсы на 5м TF\n"
        "Ищу паттерн истощения для шорта"
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

"""API layer: HTTP fetching and parsing from KuCoin and CoinMarketCap.

Isolates all network I/O so the scanner (top-gainers.py) stays focused on
analysis and reporting. Only the Python standard library is used.
"""
import json
import time
import urllib.request
from datetime import datetime, timezone

HEADERS = {"User-Agent": "Mozilla/5.0"}


def http_get_json(url):
    """Fetch a URL and parse the JSON response body."""
    req = urllib.request.Request(url, headers=HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


# ── KuCoin ──────────────────────────────────────────────────────────────────
_kucoin_symbols_cache = None


def get_kucoin_symbols():
    """Fetch all USDT trading pairs from KuCoin. Cached after first call."""
    global _kucoin_symbols_cache
    if _kucoin_symbols_cache is not None:
        return _kucoin_symbols_cache
    try:
        url = "https://api.kucoin.com/api/v1/symbols"
        data = http_get_json(url)
        symbols = data.get("data", [])
        _kucoin_symbols_cache = {
            s["baseCurrency"]: s["symbol"]
            for s in symbols
            if s.get("quoteCurrency") == "USDT" and s.get("enableTrading")
        }
    except Exception:
        _kucoin_symbols_cache = {}
    return _kucoin_symbols_cache


def _is_today_utc(ts):
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return dt == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fetch_ohlcv_kucoin(symbol, start_date=None, end_date=None):
    """
    Fetch daily klines from KuCoin public market endpoint.
    Returns list of {ts, open, high, low, close, volume} for completed candles only.
    The current forming candle is excluded.
    """
    ksym = get_kucoin_symbols().get(symbol)
    if not ksym:
        return []
    try:
        now = int(time.time())
        if end_date is None:
            end_date = now
        if start_date is None:
            start_date = now - 90 * 24 * 3600
        url = (
            f"https://api.kucoin.com/api/v1/market/candles"
            f"?symbol={ksym}&type=1day&startAt={start_date}&endAt={end_date}"
        )
        data = http_get_json(url)
        raw = data.get("data") or []
        candles = []
        for k in raw:
            if len(k) < 6:
                continue
            ts = int(k[0])
            candles.append({
                "ts": ts * 1000,
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]),
            })
        candles.sort(key=lambda x: x["ts"])
        # Exclude today's still-forming candle
        candles = [c for c in candles if not _is_today_utc(c["ts"])]
        return candles
    except Exception:
        return []


def fetch_daily_klines_since(symbol, since_date):
    """
    Fetch all completed daily klines since a given date.
    Uses KuCoin public market endpoint.
    """
    candles = fetch_ohlcv_kucoin(symbol)
    return [c for c in candles if time.strftime("%Y-%m-%d", time.gmtime(c["ts"] / 1000)) > since_date]


# ── CoinMarketCap ────────────────────────────────────────────────────────────
def fetch_top_gainers(limit=200, top_n=10, min_gain=0):
    url = (
        f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
        f"?start=1&limit={limit}&sortBy=market_cap&sortType=desc"
        f"&convert=USD&cryptoType=all&tagType=all&audited=false"
    )
    data = http_get_json(url)
    coins = data["data"]["cryptoCurrencyList"]

    results = []
    for c in coins:
        q = c["quotes"][0]
        gain = q["percentChange24h"]
        if gain > min_gain:
            results.append({
                "symbol": c["symbol"],
                "name": c["name"],
                "rank": c["cmcRank"],
                "cmc_id": c["id"],
                "price": q["price"],
                "gain_24h": gain,
                "mcap": q["marketCap"],
                "vol_24h": q["volume24h"],
            })

    results.sort(key=lambda x: x["gain_24h"], reverse=True)
    return results[:top_n]


def fetch_current_price_cmc(symbol, limit=200):
    """Fetch current price from CMC listing API (batch)."""
    url = (
        f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
        f"?start=1&limit={limit}&sortBy=market_cap&sortType=desc"
        f"&convert=USD&cryptoType=all&tagType=all&audited=false"
    )
    data = http_get_json(url)
    for c in data["data"]["cryptoCurrencyList"]:
        if c["symbol"] == symbol:
            return c["quotes"][0]["price"]
    return None


def fetch_ohlcv_cmc(cmc_id, limit=7):
    url = (
        f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail/chart"
        f"?id={cmc_id}&range=7D&interval=1d"
    )
    data = http_get_json(url)

    if "data" not in data or "points" not in data["data"]:
        return []

    points = data["data"]["points"]
    now_ms = int(time.time() * 1000)

    daily = {}
    for ts, pt in points.items():
        ts_ms = int(ts) * 1000
        if ts_ms > now_ms:
            continue
        date_str = time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000))
        v = pt["v"]
        price = v[0]
        vol = v[1] if len(v) > 1 else 0

        if date_str not in daily:
            daily[date_str] = {
                "ts": ts_ms, "open": price, "high": price,
                "low": price, "close": price, "volume": vol,
            }
        else:
            d = daily[date_str]
            d["high"] = max(d["high"], price)
            d["low"] = min(d["low"], price)
            d["close"] = price
            d["volume"] += vol
            d["ts"] = max(d["ts"], ts_ms)

    candles = [daily[k] for k in sorted(daily.keys())]
    return candles[-limit:] if len(candles) > limit else candles

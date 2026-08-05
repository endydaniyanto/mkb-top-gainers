"""
Top Gainers Bullish Candle Scanner + Paper Trading
---------------------------------------------------
Scans top 200 tokens by 24h gain for bullish daily candle setups.
Tracks open positions and closed trades with win rate / profit factor.

Criteria:
  C1: Candle N close > Candle N-1 high
  C2: Wick ratio (N high / N close) <= 1.2
  C3 (ideal): Candle N-1 is red
  C4: Candle N volume > avg volume of N-1 through N-5

Trading levels:
  Entry     = Candle N close
  Stop Loss = Candle N low
  Take Profit = Entry + (Entry - SL) x RR

Usage:
  python top-gainers.py [--top N] [--min-gain PCT] [--rr RATIO] [--output FILE] [--html] [--html-output FILE] [--update-only]
"""

import json
import urllib.request
import time
import re
import sys
import os
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
TOP_N = 10
MIN_GAIN = 0
LIMIT = 200
CANDLE_LIMIT = 7
RR = 2.0
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "top-gainers.md")
HTML_OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "top-gainers.html")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ── Price fetching ──────────────────────────────────────────────────────────
_kucoin_symbols_cache = None

def get_kucoin_symbols():
    """Fetch all USDT trading pairs from KuCoin. Cached after first call."""
    global _kucoin_symbols_cache
    if _kucoin_symbols_cache is not None:
        return _kucoin_symbols_cache
    try:
        url = "https://api.kucoin.com/api/v1/symbols"
        req = urllib.request.Request(url, headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
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
        req = urllib.request.Request(url, headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
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
    Returns list of {high, low, close, ts, date} for completed candles.
    """
    candles = fetch_ohlcv_kucoin(symbol)
    return [c for c in candles if time.strftime("%Y-%m-%d", time.gmtime(c["ts"] / 1000)) > since_date]


def fetch_current_price_cmc(symbol):
    """Fetch current price from CMC listing API (batch)."""
    url = (
        f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
        f"?start=1&limit={LIMIT}&sortBy=market_cap&sortType=desc"
        f"&convert=USD&cryptoType=all&tagType=all&audited=false"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    for c in data["data"]["cryptoCurrencyList"]:
        if c["symbol"] == symbol:
            return c["quotes"][0]["price"]
    return None


# ── Data fetching ───────────────────────────────────────────────────────────
def fetch_top_gainers(limit=200, top_n=10, min_gain=0):
    url = (
        f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
        f"?start=1&limit={limit}&sortBy=market_cap&sortType=desc"
        f"&convert=USD&cryptoType=all&tagType=all&audited=false"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
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


def fetch_ohlcv_cmc(cmc_id, limit=CANDLE_LIMIT):
    url = (
        f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail/chart"
        f"?id={cmc_id}&range=7D&interval=1d"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())

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


# ── Analysis ────────────────────────────────────────────────────────────────
def analyze(candles, rr=2.0):
    if len(candles) < 6:
        return None, f"Not enough completed candles ({len(candles)}/6 minimum)"

    n = candles[-1]
    n1 = candles[-2]
    has_ohlc = "open" in n and "high" in n

    results = {"has_ohlc": has_ohlc}

    if has_ohlc:
        results["c1"] = n["close"] > n1["high"]
        wick = n["high"] - n["close"]
        body = n["close"] - n["open"]
        results["c2_ratio"] = round(wick / body, 4) if body > 0 else 999
        results["c2"] = body > 0 and results["c2_ratio"] < 0.25
        results["c3"] = n1["close"] < n1["open"]
        results["prev_type"] = "RED" if results["c3"] else "GREEN"
        results["n_ohlc"] = (f"O={n['open']:.6g} H={n['high']:.6g} "
                             f"L={n['low']:.6g} C={n['close']:.6g}")
        results["n1_ohlc"] = (f"O={n1['open']:.6g} H={n1['high']:.6g} "
                              f"L={n1['low']:.6g} C={n1['close']:.6g}")

        results["entry"] = n["close"]
        results["stop_loss"] = n["low"]
        risk = results["entry"] - results["stop_loss"]
        results["risk"] = risk
        results["reward"] = risk * rr
        results["take_profit"] = results["entry"] + results["reward"]
        results["risk_pct"] = round((risk / results["entry"]) * 100, 1) if results["entry"] > 0 else 0
        results["reward_pct"] = round((results["reward"] / results["entry"]) * 100, 1) if results["entry"] > 0 else 0

    n_vol = n["volume"]
    prev_vols = [c["volume"] for c in candles[-6:-1]]
    avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    results["c4"] = n_vol > avg_vol if avg_vol > 0 else None
    results["c4_n_vol"] = n_vol
    results["c4_avg_vol"] = avg_vol
    results["c4_ratio"] = round(n_vol / avg_vol, 2) if avg_vol > 0 else 0

    return results, None


# ── Markdown parsing ────────────────────────────────────────────────────────
def parse_positions_from_md(filepath):
    """Parse open positions and trade history from existing markdown file."""
    open_pos = []
    history = []

    if not os.path.exists(filepath):
        return open_pos, history

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse Open Positions table
    open_section = re.search(
        r"## Open Positions.*?\n\|.*?\n\|[-| ]+\n(.*?)(?=\n##|\Z)",
        content, re.DOTALL
    )
    if open_section:
        for line in open_section.group(1).strip().split("\n"):
            if not line.startswith("|"):
                continue
            cols = [c.strip().strip("*") for c in line.split("|")[1:-1]]
            if len(cols) >= 7:
                open_pos.append({
                    "symbol": cols[0],
                    "date": cols[1],
                    "status": cols[2],
                    "entry": float(cols[3].replace("$", "")),
                    "current": cols[4] if len(cols) > 4 else "—",
                    "tp": float(cols[5].replace("$", "")),
                    "sl": float(cols[6].replace("$", "")),
                    "peak": cols[7] if len(cols) > 7 else "—",
                })

    # Parse Trade History table
    hist_section = re.search(
        r"## Trade History.*?\n\|.*?\n\|[-| ]+\n(.*?)(?=\n##|\Z)",
        content, re.DOTALL
    )
    if hist_section:
        for line in hist_section.group(1).strip().split("\n"):
            if not line.startswith("|"):
                continue
            cols = [c.strip().strip("*") for c in line.split("|")[1:-1]]
            if len(cols) >= 8:
                # Strip emojis and clean result field
                raw_result = cols[2].strip()
                if "WIN" in raw_result:
                    clean_result = "WIN"
                elif "LOSS" in raw_result:
                    clean_result = "LOSS"
                else:
                    clean_result = raw_result

                history.append({
                    "symbol": cols[0],
                    "date": cols[1],
                    "entry": float(cols[3].replace("$", "")),
                    "exit_price": float(cols[4].replace("$", "")),
                    "result": clean_result,
                    "pnl_pct": cols[5],
                    "peak": cols[6],
                    "exit_type": cols[7],
                })

    return open_pos, history


# ── Markdown generation ────────────────────────────────────────────────────
def generate_markdown(new_positions, open_positions, history, now, rr):
    lines = []
    lines.append("# Top Gainers Bullish Candle Scan")
    lines.append("")
    lines.append(f"**Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Source:** CoinMarketCap top {LIMIT} by market cap, sorted by 24h gain")
    lines.append(f"**Risk-Reward:** 1:{rr:.0f}")
    lines.append("")

    # ── Stats ──
    wins = [t for t in history if t["result"] == "WIN"]
    losses = [t for t in history if t["result"] == "LOSS"]
    total = len(wins) + len(losses)
    win_rate = (len(wins) / total * 100) if total > 0 else 0

    total_risk = 0
    total_reward = 0
    for t in history:
        entry = t["entry"]
        risk = entry - t["entry"] / (1 + rr) if False else 0  # placeholder
    # Calculate from actual data
    gross_profit = 0
    gross_loss = 0
    for t in history:
        entry = t["entry"]
        exit_p = t["exit_price"]
        if t["result"] == "WIN":
            gross_profit += exit_p - entry
        else:
            gross_loss += entry - exit_p
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

    lines.append("## Stats")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Trades | {total} |")
    lines.append(f"| Wins | {len(wins)} |")
    lines.append(f"| Losses | {len(losses)} |")
    lines.append(f"| Win Rate | {win_rate:.1f}% |")
    lines.append(f"| Gross Profit | ${gross_profit:.4g} |")
    lines.append(f"| Gross Loss | ${gross_loss:.4g} |")
    pf_str = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"
    lines.append(f"| Profit Factor | {pf_str} |")
    lines.append(f"| Open Positions | {len(open_positions)} |")
    lines.append("")

    # ── Open Positions ──
    if open_positions:
        lines.append("## Open Positions")
        lines.append("")
        lines.append("| Token | Date | Status | Entry | Current | TP | SL | Peak |")
        lines.append("|-------|------|--------|-------|---------|----|----|------|")
        for p in open_positions:
            peak = p.get("peak", "—")
            lines.append(
                f"| **{p['symbol']}** | {p['date']} | {p['status']} | ${p['entry']:.4g} "
                f"| {p['current']} | ${p['tp']:.4g} | ${p['sl']:.4g} | {peak} |"
            )
        lines.append("")

    # ── New Entries Today ──
    if new_positions:
        lines.append("## New Entries")
        lines.append("")
        lines.append("| Token | Rank | 24h | Entry | SL | TP (2R) | Risk | Reward |")
        lines.append("|-------|------|-----|-------|----|---------|------|--------|")
        for r in new_positions:
            lines.append(
                f"| **{r['symbol']}** | #{r['rank']} | +{r['gain_24h']:.1f}% "
                f"| ${r['entry']:.4g} | ${r['stop_loss']:.4g} | ${r['take_profit']:.4g} "
                f"| -{r['risk_pct']:.1f}% | +{r['reward_pct']:.1f}% |"
            )
        lines.append("")

    # ── Trade History ──
    if history:
        lines.append("## Trade History")
        lines.append("")
        lines.append("| Token | Date | Result | Entry | Exit | PnL | Peak | Type |")
        lines.append("|-------|------|--------|-------|------|-----|------|------|")
        for t in reversed(history):
            emoji = "✅" if t["result"] == "WIN" else "❌"
            peak = t.get("peak", "—")
            lines.append(
                f"| {t['symbol']} | {t['date']} | {emoji} {t['result']} "
                f"| ${t['entry']:.4g} | ${t['exit_price']:.4g} | {t['pnl_pct']} | {peak} | {t['exit_type']} |"
            )
        lines.append("")

    # ── Today's Scan (non-trading) ──
    lines.append("---")
    lines.append(f"*Generated by top-gainers.py at {now.strftime('%Y-%m-%d %H:%M UTC')}*")

    return "\n".join(lines)


# ── HTML generation ─────────────────────────────────────────────────────────
def generate_html(new_positions, open_positions, history, now, rr):
    def token_cell(symbol):
        url = f"https://www.kucoin.com/trade/{symbol}-USDT"
        return f'<a href="{url}" target="_blank">{symbol}</a>'

    wins = [t for t in history if t["result"] == "WIN"]
    losses = [t for t in history if t["result"] == "LOSS"]
    total = len(wins) + len(losses)
    win_rate = (len(wins) / total * 100) if total > 0 else 0

    gross_profit = 0
    gross_loss = 0
    for t in history:
        entry = t["entry"]
        exit_p = t["exit_price"]
        if t["result"] == "WIN":
            gross_profit += exit_p - entry
        else:
            gross_loss += entry - exit_p
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0
    pf_str = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"

    # Build open positions rows
    open_rows = ""
    for p in open_positions:
        current_html = p.get("current", "—")
        # Color the current price based on PnL
        current_class = ""
        if "(" in current_html and "%" in current_html:
            if "+" in current_html:
                current_class = "pnl-pos"
            elif "-" in current_html:
                current_class = "pnl-neg"
        open_rows += f"""<tr>
            <td class="token">{token_cell(p['symbol'])}</td>
            <td>{p['date']}</td>
            <td><span class="status-open">{p['status']}</span></td>
            <td>${p['entry']:.4g}</td>
            <td class="{current_class}">{current_html.split('(')[-1].rstrip(')') if '(' in current_html else current_html}</td>
            <td>{(p['tp'] - p['entry']) / p['entry'] * 100:.1f}%</td>
            <td>{(p['sl'] - p['entry']) / p['entry'] * 100:.1f}%</td>
            <td>{p.get('peak', '—').split('(')[-1].rstrip(')') if '(' in p.get('peak', '') else p.get('peak', '—')}</td>
        </tr>\n"""

    # Build new entries rows
    new_rows = ""
    for r in new_positions:
        new_rows += f"""<tr>
            <td class="token">{token_cell(r['symbol'])}</td>
            <td>#{r['rank']}</td>
            <td class="pnl-pos">+{r['gain_24h']:.1f}%</td>
            <td>${r['entry']:.4g}</td>
            <td class="pnl-neg">-{(r['entry'] - r['stop_loss']) / r['entry'] * 100:.1f}%</td>
            <td class="pnl-pos">+{(r['take_profit'] - r['entry']) / r['entry'] * 100:.1f}%</td>
            <td class="pnl-neg">-{r['risk_pct']:.1f}%</td>
            <td class="pnl-pos">+{r['reward_pct']:.1f}%</td>
        </tr>\n"""

    # Build history rows
    history_rows = ""
    for t in reversed(history):
        result_class = "pnl-pos" if t["result"] == "WIN" else "pnl-neg"
        emoji = "✅" if t["result"] == "WIN" else "❌"
        history_rows += f"""<tr>
            <td class="token">{token_cell(t['symbol'])}</td>
            <td>{t['date']}</td>
            <td class="{result_class}">{emoji} {t['result']}</td>
            <td>${t['entry']:.4g}</td>
            <td>${t['exit_price']:.4g}</td>
            <td class="{result_class}">{t['pnl_pct']}</td>
            <td>{t.get('peak', '—').split('(')[-1].rstrip(')') if '(' in t.get('peak', '') else t.get('peak', '—')}</td>
            <td>{t['exit_type']}</td>
        </tr>\n"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crypto Trading: Top Gainers Strategy</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e1e4e8; padding: 24px; }}
h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
.meta {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 20px; }}
.meta a {{ color: #58a6ff; text-decoration: none; }}
.meta a:hover {{ text-decoration: underline; }}
.stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }}
.stat {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px 18px; min-width: 130px; }}
.stat .label {{ font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
.stat .value {{ font-size: 1.4rem; font-weight: 600; margin-top: 4px; }}
.section {{ margin-bottom: 28px; }}
.section h2 {{ font-size: 1.1rem; margin-bottom: 10px; color: #c9d1d9; border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
th {{ background: #161b22; color: #8b949e; text-align: left; padding: 8px 10px; font-weight: 500; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.5px; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #21262d; }}
tr:hover {{ background: #161b22; }}
.token {{ font-weight: 600; color: #58a6ff; }}
.token a {{ color: inherit; text-decoration: none; }}
.token a:hover {{ text-decoration: underline; }}
.pnl-pos {{ color: #3fb950; }}
.pnl-neg {{ color: #f85149; }}
.status-open {{ background: #d29922; color: #000; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
.footer {{ color: #484f58; font-size: 0.75rem; margin-top: 20px; text-align: center; }}
.empty {{ color: #484f58; font-style: italic; padding: 16px 0; }}
</style>
</head>
<body>

<h1>Crypto Trading: Top Gainers Strategy</h1>
<div class="meta">
    {now.strftime('%Y-%m-%d %H:%M UTC')}. Strategy by <a href="https://x.com/mkbijaksana" target="_blank">@mkbijaksana</a>. Dashboard by <a href="https://x.com/endybtc" target="_blank">@endybtc</a>
</div>

<div class="stats">
    <div class="stat">
        <div class="label">Total Trades</div>
        <div class="value">{total}</div>
    </div>
    <div class="stat">
        <div class="label">Win Rate</div>
        <div class="value" style="color: {'#3fb950' if win_rate >= 50 else '#f85149'}">{win_rate:.1f}%</div>
    </div>
    <div class="stat">
        <div class="label">Profit Factor</div>
        <div class="value" style="color: {'#3fb950' if profit_factor >= 1.5 else '#d29922' if profit_factor >= 1 else '#f85149'}">{pf_str}</div>
    </div>
    <div class="stat">
        <div class="label">Open Positions</div>
        <div class="value">{len(open_positions)}</div>
    </div>
    <div class="stat">
        <div class="label">Gross Profit</div>
        <div class="value pnl-pos">${gross_profit:.4g}</div>
    </div>
    <div class="stat">
        <div class="label">Gross Loss</div>
        <div class="value pnl-neg">${gross_loss:.4g}</div>
    </div>
</div>

<div class="section">
    <h2>Open Positions</h2>
    {"<table><tr><th>Token</th><th>Date</th><th>Status</th><th>Entry</th><th>Current%</th><th>TP%</th><th>SL%</th><th>Peak%</th></tr>" + open_rows + "</table>" if open_positions else '<div class="empty">No open positions</div>'}
</div>

<div class="section">
    <h2>New Entries</h2>
    {"<table><tr><th>Token</th><th>Rank</th><th>24h</th><th>Entry</th><th>SL%</th><th>TP%</th><th>Risk</th><th>Reward</th></tr>" + new_rows + "</table>" if new_positions else '<div class="empty">No new entries this scan</div>'}
</div>

<div class="section">
    <h2>Trade History</h2>
    {"<table><tr><th>Token</th><th>Date</th><th>Result</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Peak</th><th>Type</th></tr>" + history_rows + "</table>" if history else '<div class="empty">No completed trades yet</div>'}
</div>

<div class="footer">Generated by top-gainers.py at {now.strftime('%Y-%m-%d %H:%M UTC')}</div>

</body>
</html>"""
    return html


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)

    top_n = TOP_N
    min_gain = MIN_GAIN
    rr = RR
    output = OUTPUT_FILE
    html_output = HTML_OUTPUT_FILE
    generate_html_flag = True
    update_only = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1]); i += 2
        elif args[i] == "--min-gain" and i + 1 < len(args):
            min_gain = float(args[i + 1]); i += 2
        elif args[i] == "--rr" and i + 1 < len(args):
            rr = float(args[i + 1]); i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output = args[i + 1]; i += 2
        elif args[i] == "--html":
            generate_html_flag = True; i += 1
        elif args[i] == "--html-output" and i + 1 < len(args):
            html_output = args[i + 1]; generate_html_flag = True; i += 2
        elif args[i] == "--update-only":
            update_only = True; generate_html_flag = True; i += 1
        else:
            i += 1

    print("=" * 85)
    print("TOP GAINERS BULLISH CANDLE SCANNER + PAPER TRADING")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 85)
    print()

    # ── Step 1: Load existing positions ──
    open_pos, history = parse_positions_from_md(output)
    print(f"  Open positions: {len(open_pos)}  |  Trade history: {len(history)}")

    # ── Step 2: Check open positions against current prices ──
    still_open = []
    newly_closed = []

    if open_pos:
        print("\n  Checking open positions...")
        for p in open_pos:
            time.sleep(0.2)
            klines = fetch_daily_klines_since(p["symbol"], p["date"])

            if not klines:
                print(f"    {p['symbol']:8s} — no klines after entry, keeping open")
                p["current"] = "N/A"
                p["peak"] = "N/A"
                still_open.append(p)
                continue

            # Calculate peak high across all days since entry
            peak_high = max(k["high"] for k in klines)
            peak_pct = (peak_high - p["entry"]) / p["entry"] * 100
            p["peak"] = f"${peak_high:.4g} (+{peak_pct:.1f}%)"

            # Check latest day for TP/SL
            latest = klines[-1]
            day_high = latest["high"]
            day_low = latest["low"]
            day_close = latest["close"]

            hit_tp = day_high >= p["tp"]
            hit_sl = day_low <= p["sl"]

            if hit_sl:
                risk_pct = (p["entry"] - p["sl"]) / p["entry"] * 100
                print(f"    {p['symbol']:8s} — SL HIT (day low ${day_low:.4g} <= SL ${p['sl']:.4g}) ❌ -{risk_pct:.1f}%  Peak: {p['peak']}")
                history.append({
                    "symbol": p["symbol"],
                    "date": p["date"],
                    "entry": p["entry"],
                    "exit_price": p["sl"],
                    "result": "LOSS",
                    "pnl_pct": f"-{risk_pct:.1f}%",
                    "exit_type": "SL",
                    "peak": p.get("peak", "—"),
                })
                newly_closed.append({**p, "result": "LOSS", "exit": "SL"})
            elif hit_tp:
                reward_pct = (p["tp"] - p["entry"]) / p["entry"] * 100
                print(f"    {p['symbol']:8s} — TP HIT (day high ${day_high:.4g} >= TP ${p['tp']:.4g}) ✅ +{reward_pct:.1f}%  Peak: {p['peak']}")
                history.append({
                    "symbol": p["symbol"],
                    "date": p["date"],
                    "entry": p["entry"],
                    "exit_price": p["tp"],
                    "result": "WIN",
                    "pnl_pct": f"+{reward_pct:.1f}%",
                    "exit_type": "TP",
                    "peak": p.get("peak", "—"),
                })
                newly_closed.append({**p, "result": "WIN", "exit": "TP"})
            else:
                # Secondary exit: two consecutive red daily closes
                if len(klines) >= 2:
                    last_two = klines[-2:]
                    both_red = all(c["close"] < c["open"] for c in last_two)
                    if both_red:
                        exit_price = day_close
                        pnl_pct = (exit_price - p["entry"]) / p["entry"] * 100
                        pnl_result = "WIN" if pnl_pct >= 0 else "LOSS"
                        pnl_str = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"
                        print(f"    {p['symbol']:8s} — 2 RED CLOSES EXIT at ${exit_price:.4g} {'✅' if pnl_result == 'WIN' else '❌'} {pnl_str}  Peak: {p['peak']}")
                        history.append({
                            "symbol": p["symbol"],
                            "date": p["date"],
                            "entry": p["entry"],
                            "exit_price": exit_price,
                            "result": pnl_result,
                            "pnl_pct": pnl_str,
                            "exit_type": "2 RED",
                            "peak": p.get("peak", "—"),
                        })
                        newly_closed.append({**p, "result": pnl_result, "exit": "2 RED"})
                        continue

                unrealized = (day_close - p["entry"]) / p["entry"] * 100
                p["current"] = f"${day_close:.4g} ({unrealized:+.1f}%)"
                print(f"    {p['symbol']:8s} — open, close ${day_close:.4g} ({unrealized:+.1f}%)  Peak: {p['peak']}")
                still_open.append(p)

    # ── Step 3: Scan for new qualifying tokens ──
    new_entries = []
    scan_results = []
    if update_only:
        print("\n  --update-only: skipping new token scan")
    else:
        print(f"\n  Scanning top {LIMIT} for new entries...")
        gainers = fetch_top_gainers(limit=LIMIT, top_n=top_n, min_gain=min_gain)
        print(f"  Found {len(gainers)} gainers above {min_gain}% 24h gain.")
        print(f"  C1: close > prev high, C2: wick/body < 0.25, C4: vol > 5d avg")
        print(f"  Source: KuCoin public market API\n")

        existing_symbols = {p["symbol"] for p in still_open}
        existing_symbols.update(t["symbol"] for t in history)

        print(f"  {'Token':8s} {'Gain':>6s} {'C1':4s} {'C2':>8s} {'C4':>6s}  Prev   RL  Status")
        print(f"  {'-'*74}")

        for g in gainers:
            if g["symbol"] in existing_symbols:
                continue

            # Fetch candle data from KuCoin
            time.sleep(0.3)
            source = "KuCoin"
            candles = fetch_ohlcv_kucoin(g["symbol"])

            if not candles or len(candles) < 6:
                scan_results.append((g["symbol"], g["gain_24h"], "NO KUCOIN", "-", "-", "-", "-", "-", "-"))
                continue

            result, err = analyze(candles, rr=rr)
            if err or not result["has_ohlc"]:
                scan_results.append((g["symbol"], g["gain_24h"], "NO OHLC", "-", "-", "-", "-", "-", "-"))
                continue

            c1_str = "Y" if result["c1"] else "N"
            c2_str = f"{result['c2_ratio']:.2f}" if result["c2_ratio"] < 999 else "RED"
            c4_str = f"{result['c4_ratio']:.1f}x"
            status = "QUALIFIES" if (result["c1"] and result["c2"] and result["c4"]) else "FAIL"
            scan_results.append((g["symbol"], g["gain_24h"], c1_str, c2_str, c4_str, status,
                                 result.get("n_ohlc", "-"), result.get("prev_type", "-"), f"{result.get('risk_pct', 0):.1f}%"))

            if result["c1"] and result["c2"] and result["c4"]:
                tag = ">>>"
                print(f"  {tag} {g['symbol']:8s} +{g['gain_24h']:.1f}%  Entry=${result['entry']:.4g}  "
                      f"SL=${result['stop_loss']:.4g} TP=${result['take_profit']:.4g}")
                new_entries.append({
                    **g,
                    "status": "BULLISH",
                    "source": source,
                    "n_date": datetime.fromtimestamp(candles[-1]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "c1": result["c1"],
                    "c2": result["c2"],
                    "c2_ratio": result["c2_ratio"],
                    "c3": result["c3"],
                    "c4": result["c4"],
                    "c4_ratio": result["c4_ratio"],
                    "entry": result["entry"],
                    "stop_loss": result["stop_loss"],
                    "take_profit": result["take_profit"],
                    "risk_pct": result["risk_pct"],
                    "reward_pct": result["reward_pct"],
                })

        for item in scan_results:
            if len(item) == 6:
                sym, gain, c1, c2, c4, status = item
                print(f"  {sym:8s} +{gain:>5.1f}% {c1:4s} {c2:>8s} {c4:>6s}        {status}")
            else:
                sym, gain, c1, c2, c4, status, ohlc, prev, risk = item
                print(f"  {sym:8s} +{gain:>5.1f}% {c1:4s} {c2:>8s} {c4:>6s}  {prev:5s} {risk:>5s}  {status}")
                if status != "QUALIFIES":
                    print(f"           last: {ohlc}")

    # Add new entries to open positions
    for ne in new_entries:
        still_open.append({
            "symbol": ne["symbol"],
            "date": ne["n_date"],
            "entry": ne["entry"],
            "sl": ne["stop_loss"],
            "tp": ne["take_profit"],
            "status": "OPEN",
            "current": "—",
        })

    # ── Step 4: Write updated markdown ──
    md = generate_markdown(new_entries, still_open, history, now, rr)
    with open(output, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n  Results saved to: {output}")

    # ── Step 4b: Generate HTML dashboard if requested ──
    if generate_html_flag:
        html = generate_html(new_entries, still_open, history, now, rr)
        with open(html_output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Dashboard saved to: {html_output}")

    # ── Summary ──
    wins = len([t for t in history if t["result"] == "WIN"])
    losses = len([t for t in history if t["result"] == "LOSS"])
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0

    print(f"\n  OPEN: {len(still_open)}  |  NEW: {len(new_entries)}  |  "
          f"CLOSED TODAY: {len(newly_closed)}  |  TOTAL TRADES: {total}  |  "
          f"WIN RATE: {wr:.1f}%")

    if newly_closed:
        print(f"\n  Closed today:")
        for t in newly_closed:
            emoji = "✅" if t["result"] == "WIN" else "❌"
            print(f"    {emoji} {t['symbol']} {t['exit']}")

    print()
    print("-" * 85)
    print("Done.")


if __name__ == "__main__":
    main()

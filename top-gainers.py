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
  python top-gainers.py [--top N] [--min-gain PCT] [--rr RATIO] [--output FILE] [--html-output FILE] [--update-only]
"""

import time
import sys
import os
from datetime import datetime, timezone

from api import (
    fetch_ohlcv_kucoin,
    fetch_daily_klines_since,
    fetch_top_gainers,
)
from report import (
    parse_positions_from_md,
    generate_markdown,
    generate_html,
)
from strategy import analyze

# ── Config ──────────────────────────────────────────────────────────────────
TOP_N = 10
MIN_GAIN = 0
LIMIT = 200
RR = 2.0
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "top-gainers.md")
HTML_OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)

    top_n = TOP_N
    min_gain = MIN_GAIN
    rr = RR
    output = OUTPUT_FILE
    html_output = HTML_OUTPUT_FILE
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
        elif args[i] == "--html-output" and i + 1 < len(args):
            html_output = args[i + 1]; i += 2
        elif args[i] == "--update-only":
            update_only = True; i += 1
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

    # ── Step 4b: Generate HTML dashboard ──
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

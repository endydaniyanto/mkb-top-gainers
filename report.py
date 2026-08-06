"""Reporting layer: parse prior runs and render markdown + HTML dashboards."""
import os
import re

LIMIT = 200


def compute_stats(history):
    """Aggregate trade stats (win rate, PnL, profit factor) from history."""
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

    return {
        "wins": wins,
        "losses": losses,
        "total": total,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
    }


def compute_cumulative_roi(history):
    """Cumulative ROI (equal-weight $1/trade) after each closed trade.

    Returns a dict keyed by (symbol, date) -> cumulative ROI % at that trade's
    chronological position. ROI per trade is recomputed from entry/exit_price
    (not the rounded pnl_pct string) for accuracy.
    """
    ordered = sorted(history, key=lambda t: t["date"])
    cum = {}
    running = 0.0
    count = 0
    for t in ordered:
        if not t.get("entry"):
            continue
        roi = (t["exit_price"] - t["entry"]) / t["entry"] * 100
        running += roi
        count += 1
        cum[(t["symbol"], t["date"])] = running / count
    return cum


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


def generate_markdown(new_positions, open_positions, history, now, rr, cum_roi=None):
    lines = []
    lines.append("# Top Gainers Bullish Candle Scan")
    lines.append("")
    lines.append(f"**Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Source:** CoinMarketCap top {LIMIT} by market cap, sorted by 24h gain")
    lines.append(f"**Risk-Reward:** 1:{rr:.0f}")
    lines.append("")

    # ── Stats ──
    stats = compute_stats(history)
    wins = stats["wins"]
    losses = stats["losses"]
    total = stats["total"]
    win_rate = stats["win_rate"]
    gross_profit = stats["gross_profit"]
    gross_loss = stats["gross_loss"]
    profit_factor = stats["profit_factor"]

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
        lines.append("| Token | Date | Result | Entry | Exit | PnL | Peak | Type | Cum ROI |")
        lines.append("|-------|------|--------|-------|------|-----|------|------|---------|")
        for t in reversed(history):
            emoji = "✅" if t["result"] == "WIN" else "❌"
            peak = t.get("peak", "—")
            c = cum_roi.get((t["symbol"], t["date"])) if cum_roi else None
            c_str = f"{c:+.1f}%" if c is not None else "—"
            lines.append(
                f"| {t['symbol']} | {t['date']} | {emoji} {t['result']} "
                f"| ${t['entry']:.4g} | ${t['exit_price']:.4g} | {t['pnl_pct']} | {peak} | {t['exit_type']} | {c_str} |"
            )
        lines.append("")

    # ── Today's Scan (non-trading) ──
    lines.append("---")
    lines.append(f"*Generated by top-gainers.py at {now.strftime('%Y-%m-%d %H:%M UTC')}*")

    return "\n".join(lines)


def generate_html(new_positions, open_positions, history, now, rr, cum_roi=None):
    def token_cell(symbol):
        url = f"https://www.kucoin.com/trade/{symbol}-USDT"
        return f'<a href="{url}" target="_blank">{symbol}</a>'

    stats = compute_stats(history)
    wins = stats["wins"]
    losses = stats["losses"]
    total = stats["total"]
    win_rate = stats["win_rate"]
    gross_profit = stats["gross_profit"]
    gross_loss = stats["gross_loss"]
    profit_factor = stats["profit_factor"]
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
        c = cum_roi.get((t["symbol"], t["date"])) if cum_roi else None
        c_class = "pnl-pos" if (c is not None and c >= 0) else "pnl-neg" if c is not None else ""
        c_str = f"{c:+.1f}%" if c is not None else "—"
        history_rows += f"""<tr>
            <td class="token">{token_cell(t['symbol'])}</td>
            <td>{t['date']}</td>
            <td class="{result_class}">{emoji} {t['result']}</td>
            <td>${t['entry']:.4g}</td>
            <td>${t['exit_price']:.4g}</td>
            <td class="{result_class}">{t['pnl_pct']}</td>
            <td>{t.get('peak', '—').split('(')[-1].rstrip(')') if '(' in t.get('peak', '') else t.get('peak', '—')}</td>
            <td>{t['exit_type']}</td>
            <td class="{c_class}">{c_str}</td>
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
.strategy {{ color: #adbac7; font-size: 0.9rem; line-height: 1.6; }}
.strategy p {{ margin-bottom: 12px; }}
.strategy h3 {{ font-size: 0.95rem; color: #c9d1d9; margin: 16px 0 8px; font-weight: 600; }}
.strategy ul {{ margin: 0 0 12px 18px; }}
.strategy li {{ margin-bottom: 6px; }}
.strategy strong {{ color: #e1e4e8; }}
.strategy-meta {{ color: #8b949e; font-size: 0.8rem; margin-top: 16px; border-top: 1px solid #30363d; padding-top: 12px; }}
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
        <div class="label">Open Positions</div>
        <div class="value">{len(open_positions)}</div>
    </div>
    <div class="stat">
        <div class="label">Win Rate</div>
        <div class="value" style="color: {'#3fb950' if win_rate >= 50 else '#f85149'}">{win_rate:.1f}%</div>
    </div>
    <div class="stat">
        <div class="label">Profit Factor</div>
        <div class="value" style="color: {'#3fb950' if profit_factor >= 1.5 else '#d29922' if profit_factor >= 1 else '#f85149'}">{pf_str}</div>
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
    {"<table><tr><th>Token</th><th>Date</th><th>Result</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Peak</th><th>Type</th><th>Cum ROI</th></tr>" + history_rows + "</table>" if history else '<div class="empty">No completed trades yet</div>'}
</div>

<div class="section">
    <h2>Strategy</h2>
    <div class="strategy">
        <p>A daily swing-trading scanner that hunts bullish continuation setups among the market's strongest movers.</p>
        <h3>Universe &amp; screening</h3>
        <ul>
            <li>Pulls the top 200 tokens by market cap from CoinMarketCap, ranked by 24h % gain.</li>
            <li>For each candidate, daily OHLCV candles are fetched from KuCoin.</li>
        </ul>
        <h3>Entry setup — all three must hold</h3>
        <ul>
            <li><strong>C1 — Breakout:</strong> today's close is above yesterday's high.</li>
            <li><strong>C2 — Clean candle:</strong> a small upper wick (wick-to-body ratio &lt; 0.25) — a strong body with little rejection.</li>
            <li><strong>C3 — Bearish prior candle (ideal):</strong> yesterday's candle was red/bearish.</li>
            <li><strong>C4 — Volume expansion:</strong> today's volume exceeds the average of the prior 5 days.</li>
        </ul>
        <h3>Trade levels (derived from the qualifying candle)</h3>
        <ul>
            <li><strong>Entry</strong> = that candle's close</li>
            <li><strong>Stop Loss</strong> = that candle's low</li>
            <li><strong>Take Profit</strong> = Entry + (Entry − SL) × 2 → fixed 1:2 risk-reward</li>
        </ul>
        <h3>Exit rules (checked every day)</h3>
        <ul>
            <li><strong>Stop Loss:</strong> if the day's low touches the SL → exit at SL (Loss).</li>
            <li><strong>Take Profit:</strong> if the day's high reaches the TP → exit at TP (Win).</li>
            <li><strong>Secondary exit:</strong> two consecutive red (bearish) daily closes → exit at the latest close (Win/Loss by actual PnL).</li>
            <li>Peak gain since entry is tracked for reference.</li>
        </ul>
        <h3>Portfolio behavior</h3>
        <ul>
            <li>Tokens already open or already traded are skipped on new scans.</li>
            <li>New qualifiers are logged as open positions; the dashboard tracks open positions, win rate, and profit factor over time.</li>
        </ul>
        <p class="strategy-meta">Data sources: CoinMarketCap (top gainers list) · KuCoin (daily candles &amp; price checks). Scanned automatically once per day via GitHub Actions.</p>
    </div>
</div>

<div class="footer">Generated by top-gainers.py at {now.strftime('%Y-%m-%d %H:%M UTC')}</div>

</body>
</html>"""
    return html

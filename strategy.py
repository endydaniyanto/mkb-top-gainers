"""Strategy engine: candle analysis and bullish setup detection.

Pure logic only — no network, no file I/O — so the entry criteria (C1–C4)
and derived trade levels can be unit-tested against known candle data.
"""


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

        results["entry"] = n["close"]
        results["stop_loss"] = n["low"]
        risk = results["entry"] - results["stop_loss"]
        reward = risk * rr
        results["take_profit"] = results["entry"] + reward
        results["risk_pct"] = round((risk / results["entry"]) * 100, 1) if results["entry"] > 0 else 0
        results["reward_pct"] = round((reward / results["entry"]) * 100, 1) if results["entry"] > 0 else 0

    n_vol = n["volume"]
    prev_vols = [c["volume"] for c in candles[-6:-1]]
    avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    results["c4"] = n_vol > avg_vol if avg_vol > 0 else None
    results["c4_ratio"] = round(n_vol / avg_vol, 2) if avg_vol > 0 else 0

    return results, None

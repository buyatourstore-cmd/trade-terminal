"""
Phase 1.5 — Local data proxy for the Trade Terminal.

Why this exists: browsers can't call Yahoo Finance or NSE directly (CORS +
anti-bot rules block it). This tiny local server fetches the data on your
behalf and hands it to the HTML app as plain JSON over localhost.

Run it with start_mac.sh (Mac/Linux) or start_windows.bat (Windows).
It does not need any API key and it does not store or send your data
anywhere except to your own browser.
"""

import time
import threading
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)  # allow the HTML app (opened as a local file) to call this server

# ---------------------------------------------------------------------------
# Simple in-memory cache so we don't hammer Yahoo Finance on every request.
# ---------------------------------------------------------------------------
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 10


def cached(key, ttl, fn):
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and now - entry["t"] < ttl:
            return entry["v"]
    value = fn()
    with _cache_lock:
        _cache[key] = {"t": now, "v": value}
    return value


# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------
INDEX_TICKERS = {
    "nifty": "^NSEI",
    "banknifty": "^NSEBANK",
    "vix": "^INDIAVIX",
}


def to_nse_ticker(symbol):
    symbol = symbol.strip().upper()
    if symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS"


def quote_for(ticker_symbol):
    """Return latest price + previous close for a single Yahoo ticker."""
    t = yf.Ticker(ticker_symbol)
    hist = t.history(period="2d", interval="1d")
    if hist.empty:
        # fall back to fast_info for symbols with sparse daily history
        fi = t.fast_info
        price = fi.get("lastPrice")
        prev = fi.get("previousClose")
        return {"price": price, "prev_close": prev}
    last_row = hist.iloc[-1]
    price = float(last_row["Close"])
    prev = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else price
    return {"price": price, "prev_close": prev}


def intraday_for(ticker_symbol):
    """Return today's minute bars: [{t, o, h, l, c, v}, ...]."""
    t = yf.Ticker(ticker_symbol)
    hist = t.history(period="1d", interval="1m")
    bars = []
    for idx, row in hist.iterrows():
        bars.append({
            "t": idx.isoformat(),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"]),
            "v": float(row["Volume"]),
        })
    return bars


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({"ok": True, "message": "Trade Terminal backend is running"})


@app.route("/api/indices")
def indices():
    def fetch():
        out = {}
        for key, ticker in INDEX_TICKERS.items():
            try:
                out[key] = quote_for(ticker)
            except Exception as e:
                out[key] = {"error": str(e)}
        return out
    data = cached("indices", CACHE_TTL_SECONDS, fetch)
    return jsonify(data)


@app.route("/api/quote/<symbol>")
def quote(symbol):
    ticker = to_nse_ticker(symbol)

    def fetch():
        try:
            q = quote_for(ticker)
            bars = intraday_for(ticker)
            return {"symbol": symbol.upper(), "quote": q, "bars": bars}
        except Exception as e:
            return {"symbol": symbol.upper(), "error": str(e)}

    data = cached(f"quote:{symbol}", CACHE_TTL_SECONDS, fetch)
    return jsonify(data)


@app.route("/api/watchlist")
def watchlist():
    symbols_param = request.args.get("symbols", "")
    symbols = [s.strip().upper() for s in symbols_param.split(",") if s.strip()]

    def fetch():
        out = []
        for sym in symbols:
            ticker = to_nse_ticker(sym)
            try:
                q = quote_for(ticker)
                price = q["price"]
                prev = q["prev_close"]
                chg_pct = ((price - prev) / prev * 100) if prev else 0
                out.append({"symbol": sym, "price": price, "chg_pct": chg_pct})
            except Exception as e:
                out.append({"symbol": sym, "error": str(e)})
        return out

    key = "watchlist:" + ",".join(sorted(symbols))
    data = cached(key, CACHE_TTL_SECONDS, fetch)
    return jsonify(data)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5057))
    print("\nTrade Terminal backend starting...")
    app.run(host="0.0.0.0", port=port, debug=False)

"""
Trading Bot Web App
- Bot runs in a background thread
- Flask serves a live dashboard at /
- Auto-refreshes every 30 seconds
"""

import os
import time
import threading
import pandas as pd
from datetime import datetime
from flask import Flask, render_template_string, jsonify
from binance.client import Client
from dotenv import load_dotenv
import ta

load_dotenv()

app = Flask(__name__)

# ===== SHARED STATE (thread-safe via lock) =====
lock = threading.Lock()
STATE = {
    "balance": 50.0,
    "positions": {},
    "trade_log": [],
    "hold_cycles": {},
    "cycle": 0,
    "last_updated": None,
    "status": "Starting...",
    "watching": [],
    "total_value": 50.0,
    "pnl": 0.0,
    "bot_running": False,
}

# ===== SETTINGS =====
INTERVAL          = "1h"
BALANCE_FLOOR     = 15.0
POSITION_SIZE_PCT = 0.20
MAX_COINS         = 5
STOP_LOSS_PCT     = 0.05
MAX_HOLD_CYCLES   = 10
STARTING_BALANCE  = 50.0

SAFE_COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT", "LINKUSDT",
    "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT", "FTMUSDT",
]

# ===== BOT LOGIC =====

def get_client():
    return Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET"))

def get_top_coins(client):
    try:
        tickers = client.get_ticker()
        ticker_map = {t["symbol"]: t for t in tickers}
        candidates = []
        for symbol in SAFE_COINS:
            if symbol not in ticker_map:
                continue
            t = ticker_map[symbol]
            if float(t["quoteVolume"]) > 50_000_000 and abs(float(t["priceChangePercent"])) < 15:
                candidates.append(symbol)
        return candidates[:MAX_COINS]
    except Exception as e:
        log(f"get_top_coins error: {e}")
        return []

def get_data(client, symbol):
    try:
        klines = client.get_klines(symbol=symbol, interval=INTERVAL, limit=100)
        df = pd.DataFrame(klines, columns=[
            "time","open","high","low","close","volume",
            "close_time","qav","trades","tbbav","tbqav","ignore"
        ])
        df["close"] = df["close"].astype(float)
        return df
    except Exception as e:
        log(f"Data fetch failed for {symbol}: {e}")
        return None

def add_indicators(df):
    df["rsi"]         = ta.momentum.RSIIndicator(df["close"]).rsi()
    df["ema"]         = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
    macd              = ta.trend.MACD(df["close"])
    df["macd"]        = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    return df

def strategy(df, symbol, current_price, positions, hold_cycles):
    latest = df.iloc[-1]
    rsi    = latest["rsi"]
    macd   = latest["macd"]
    signal = latest["macd_signal"]
    already_holding = symbol in positions and positions[symbol]["qty"] > 0

    if already_holding:
        buy_price   = positions[symbol]["buy_price"]
        loss_pct    = (buy_price - current_price) / buy_price
        hold_count  = hold_cycles.get(symbol, 0)
        if loss_pct >= STOP_LOSS_PCT:
            return "SELL", f"Stop loss -{loss_pct*100:.1f}%"
        if hold_count >= MAX_HOLD_CYCLES:
            return "SELL", f"Time stop ({hold_count} cycles)"

    if not already_holding:
        if rsi < 35 and macd > signal:
            return "BUY", f"RSI {rsi:.1f} + MACD"

    if already_holding:
        if rsi > 65:
            return "SELL", f"RSI overbought {rsi:.1f}"

    return "HOLD", f"RSI {rsi:.1f}"

def execute_trade(symbol, signal, reason, price, state):
    if symbol not in state["positions"]:
        state["positions"][symbol] = {"qty": 0.0, "buy_price": 0.0}
    if symbol not in state["hold_cycles"]:
        state["hold_cycles"][symbol] = 0

    if signal == "BUY":
        if state["balance"] <= BALANCE_FLOOR:
            return
        available = state["balance"] - BALANCE_FLOOR
        amount = available * POSITION_SIZE_PCT
        if amount < 5:
            return
        qty = amount / price
        state["positions"][symbol]["qty"]       += qty
        state["positions"][symbol]["buy_price"]  = price
        state["balance"]                        -= amount
        state["hold_cycles"][symbol]             = 0
        state["trade_log"].insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": "BUY",
            "symbol": symbol,
            "price": price,
            "amount": amount,
            "profit": None,
            "reason": reason,
        })

    elif signal == "SELL" and state["positions"][symbol]["qty"] > 0:
        qty    = state["positions"][symbol]["qty"]
        amount = qty * price
        profit = amount - (qty * state["positions"][symbol]["buy_price"])
        state["balance"]                   += amount
        state["positions"][symbol]["qty"]   = 0.0
        state["hold_cycles"][symbol]        = 0
        state["trade_log"].insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": "SELL",
            "symbol": symbol,
            "price": price,
            "amount": amount,
            "profit": profit,
            "reason": reason,
        })

    if state["positions"][symbol]["qty"] > 0:
        state["hold_cycles"][symbol] = state["hold_cycles"].get(symbol, 0) + 1

    # Keep log to last 100 entries
    state["trade_log"] = state["trade_log"][:100]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ===== BOT THREAD =====
def bot_loop():
    log("Bot thread started")
    client = get_client()

    with lock:
        STATE["bot_running"] = True

    while True:
        try:
            with lock:
                STATE["cycle"] += 1
                cycle = STATE["cycle"]

            log(f"--- Cycle #{cycle} ---")
            symbols = get_top_coins(client)

            with lock:
                STATE["watching"] = symbols
                STATE["status"] = f"Running — cycle #{cycle}"

            if not symbols:
                with lock:
                    STATE["status"] = "No coins found, retrying..."
                time.sleep(60)
                continue

            total_value = 0.0

            for symbol in symbols:
                df = get_data(client, symbol)
                if df is None:
                    continue
                df    = add_indicators(df)
                price = df["close"].iloc[-1]

                if price < 0.10:
                    continue

                with lock:
                    sig, reason = strategy(df, symbol, price, STATE["positions"], STATE["hold_cycles"])
                    execute_trade(symbol, sig, reason, price, STATE)
                    coin_value = STATE["positions"].get(symbol, {}).get("qty", 0) * price
                    total_value += coin_value

                log(f"  {symbol}: {sig} @ ${price:.4f} ({reason})")

            with lock:
                total_value    += STATE["balance"]
                STATE["total_value"]  = total_value
                STATE["pnl"]          = total_value - STARTING_BALANCE
                STATE["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if STATE["balance"] < BALANCE_FLOOR:
                    STATE["status"] = "STOPPED — balance floor reached"
                    STATE["bot_running"] = False
                    log("Balance floor reached, bot stopping.")
                    break

            time.sleep(60)

        except Exception as e:
            log(f"Cycle error: {e}")
            with lock:
                STATE["status"] = f"Error: {e} — retrying..."
            time.sleep(30)

# ===== DASHBOARD HTML =====
DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trading Bot Dashboard</title>
  <meta http-equiv="refresh" content="30">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 24px; }
    h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
    .sub { font-size: 13px; color: #64748b; margin-bottom: 24px; }

    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
             gap: 12px; margin-bottom: 24px; }
    .card { background: #1e2330; border-radius: 12px; padding: 16px 20px; }
    .card .label { font-size: 12px; color: #64748b; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.05em; }
    .card .value { font-size: 24px; font-weight: 600; }
    .card .value.green  { color: #34d399; }
    .card .value.red    { color: #f87171; }
    .card .value.yellow { color: #fbbf24; }
    .card .value.white  { color: #f1f5f9; }

    .status-bar { background: #1e2330; border-radius: 10px; padding: 12px 16px;
                  font-size: 13px; color: #94a3b8; margin-bottom: 24px;
                  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
    .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
           background: #34d399; margin-right: 6px; animation: pulse 2s infinite; }
    .dot.stopped { background: #f87171; animation: none; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

    .section { background: #1e2330; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
    .section h2 { font-size: 14px; font-weight: 600; color: #94a3b8;
                  text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 16px; }

    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; color: #475569; font-weight: 500; padding: 6px 12px 10px;
         border-bottom: 1px solid #2d3748; }
    td { padding: 9px 12px; border-bottom: 1px solid #1a1f2e; }
    tr:last-child td { border-bottom: none; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
             font-size: 11px; font-weight: 600; letter-spacing: 0.04em; }
    .badge.BUY  { background: #064e3b; color: #34d399; }
    .badge.SELL { background: #450a0a; color: #f87171; }
    .badge.HOLD { background: #1e2330; color: #64748b; border: 1px solid #2d3748; }
    .profit.pos { color: #34d399; }
    .profit.neg { color: #f87171; }

    .empty { color: #475569; font-size: 13px; text-align: center; padding: 24px; }
    .refresh { font-size: 12px; color: #475569; }
  </style>
</head>
<body>
  <h1>Trading Bot Dashboard</h1>
  <p class="sub">Paper trading — Binance market data</p>

  <div class="cards">
    <div class="card">
      <div class="label">Balance</div>
      <div class="value white">${{ "%.2f"|format(state.balance) }}</div>
    </div>
    <div class="card">
      <div class="label">Portfolio Value</div>
      <div class="value white">${{ "%.2f"|format(state.total_value) }}</div>
    </div>
    <div class="card">
      <div class="label">Total P&amp;L</div>
      <div class="value {{ 'green' if state.pnl >= 0 else 'red' }}">
        {{ "%+.2f"|format(state.pnl) }}
      </div>
    </div>
    <div class="card">
      <div class="label">Cycles Run</div>
      <div class="value yellow">{{ state.cycle }}</div>
    </div>
    <div class="card">
      <div class="label">Open Positions</div>
      <div class="value white">
        {{ state.positions.values()|selectattr("qty", "gt", 0)|list|length }}
      </div>
    </div>
    <div class="card">
      <div class="label">Trades Made</div>
      <div class="value white">{{ state.trade_log|length }}</div>
    </div>
  </div>

  <div class="status-bar">
    <span>
      <span class="dot {{ '' if state.bot_running else 'stopped' }}"></span>
      {{ state.status }}
    </span>
    <span class="refresh">
      Last updated: {{ state.last_updated or "not yet" }} &nbsp;·&nbsp; Auto-refreshes every 30s
    </span>
  </div>

  <!-- Open Positions -->
  <div class="section">
    <h2>Open Positions</h2>
    {% set open = [] %}
    {% for sym, pos in state.positions.items() if pos.qty > 0 %}
      {% set _ = open.append((sym, pos)) %}
    {% endfor %}
    {% if open %}
    <table>
      <tr><th>Coin</th><th>Qty</th><th>Buy Price</th><th>Held (cycles)</th></tr>
      {% for sym, pos in open %}
      <tr>
        <td>{{ sym }}</td>
        <td>{{ "%.6f"|format(pos.qty) }}</td>
        <td>${{ "%.4f"|format(pos.buy_price) }}</td>
        <td>{{ state.hold_cycles.get(sym, 0) }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No open positions</div>
    {% endif %}
  </div>

  <!-- Watching -->
  <div class="section">
    <h2>Watching This Cycle</h2>
    {% if state.watching %}
    <div style="display:flex; flex-wrap:wrap; gap:8px;">
      {% for s in state.watching %}
      <span class="badge HOLD">{{ s }}</span>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty">No coins selected yet</div>
    {% endif %}
  </div>

  <!-- Trade Log -->
  <div class="section">
    <h2>Trade Log (last 50)</h2>
    {% if state.trade_log %}
    <table>
      <tr><th>Time</th><th>Action</th><th>Coin</th><th>Price</th><th>Amount</th><th>P&amp;L</th><th>Reason</th></tr>
      {% for t in state.trade_log[:50] %}
      <tr>
        <td style="color:#64748b">{{ t.time }}</td>
        <td><span class="badge {{ t.action }}">{{ t.action }}</span></td>
        <td>{{ t.symbol }}</td>
        <td>${{ "%.4f"|format(t.price) }}</td>
        <td>${{ "%.2f"|format(t.amount) }}</td>
        <td>
          {% if t.profit is not none %}
          <span class="profit {{ 'pos' if t.profit >= 0 else 'neg' }}">
            {{ "%+.2f"|format(t.profit) }}
          </span>
          {% else %} — {% endif %}
        </td>
        <td style="color:#64748b">{{ t.reason }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No trades yet — waiting for signals...</div>
    {% endif %}
  </div>

</body>
</html>
"""

# ===== ROUTES =====
@app.route("/")
def dashboard():
    with lock:
        snap = dict(STATE)
    return render_template_string(DASHBOARD, state=snap)

@app.route("/api/state")
def api_state():
    with lock:
        snap = dict(STATE)
    snap["positions"]  = {k: dict(v) for k, v in snap["positions"].items()}
    snap["trade_log"]  = snap["trade_log"][:50]
    return jsonify(snap)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "bot_running": STATE["bot_running"]})

# ===== STARTUP =====
def start():
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()

if __name__ == "__main__":
    start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

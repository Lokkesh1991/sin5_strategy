print("🚀 Starting tradingview_zerodha_sin5.py...")

from flask import Flask, request, jsonify
from kiteconnect import KiteConnect
import logging
import os
import json
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re
import csv
import random

# === Paper Trading Mode ===
PAPER_TRADING = True

# === Load .env ===
load_dotenv()
API_KEY = os.getenv("KITE_API_KEY")

# === Flask App ===
app = Flask(__name__)

# === Logging Setup ===
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/tradingview_zerodha.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# === Signal Tracker
signals = {}

# === Kite Connect Setup ===
def get_kite_client():
    try:
        with open("token.json") as f:
            token_data = json.load(f)
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(token_data["access_token"])
        return kite
    except Exception as e:
        logging.error(f"❌ Failed to initialize Kite client: {str(e)}")
        return None

# === Helper ===
def is_gold_symbol(symbol):
    return "GOLD" in symbol.upper()

lot_size_cache = {}
def get_lot_size(kite, tradingsymbol):
    if tradingsymbol in lot_size_cache:
        return lot_size_cache[tradingsymbol]
    try:
        instruments = kite.instruments("NFO")
        for item in instruments:
            if item["tradingsymbol"] == tradingsymbol:
                lot_size = item["lot_size"]
                lot_size_cache[tradingsymbol] = lot_size
                logging.info(f"📦 Lot size for {tradingsymbol}: {lot_size}")
                return lot_size
        return 1
    except Exception as e:
        logging.error(f"❌ Error fetching lot size: {e}")
        return 1

def get_position_quantity(kite, tradingsymbol):
    try:
        positions = kite.positions()["net"]
        for pos in positions:
            if pos["tradingsymbol"] == tradingsymbol:
                return pos["quantity"]
        return 0
    except Exception as e:
        logging.error(f"⚠️ Failed to fetch positions: {e}")
        return 0

def get_active_contract(symbol):
    today = datetime.now().date()
    current_month = today.month
    current_year = today.year
    next_month_first = datetime(current_year + int(current_month == 12), (current_month % 12) + 1, 1)
    last_day = next_month_first - timedelta(days=1)
    while last_day.weekday() != 0:
        last_day -= timedelta(days=1)
    rollover_cutoff = last_day.date() - timedelta(days=4)

    if today > rollover_cutoff:
        next_month = current_month + 1 if current_month < 12 else 1
        next_year = current_year if current_month < 12 else current_year + 1
        return f"{symbol}{str(next_year)[2:]}{datetime(next_year, next_month, 1).strftime('%b').upper()}FUT"
    else:
        return f"{symbol}{str(current_year)[2:]}{datetime(current_year, current_month, 1).strftime('%b').upper()}FUT"

def auto_rollover_positions(kite, symbol):
    if PAPER_TRADING:
        return
    # Same logic as before (not needed for paper trades)

def generate_mock_trade(symbol, signal, qty, price=None):
    now = datetime.now()
    price = price or round(random.uniform(700, 750), 2)
    trade_id = f"26{random.randint(1000000000, 9999999999)}"
    order_id = str(random.randint(100000, 999999))

    row = [
        symbol,                           # Symbol
        now.strftime('%Y-%m-%d'),         # Trade Date
        "NSE",                            # Exchange
        "FO",                             # Segment
        signal.lower(),                  # buy/sell
        "FALSE",                          # Order Type
        qty,                              # Quantity
        price,                            # Price
        trade_id,                         # Trade ID
        order_id,                         # Order ID
        now.strftime('%Y-%m-%dT%H:%M:%S'),# Trade Time
        now.strftime('%Y-%m-%d')          # Log Date
    ]
    with open("logs/paper_trades.csv", mode='a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)
    logging.info(f"📄 Paper trade logged: {row}")

def enter_position(kite, symbol, side):
    lot_qty = 1
    try:
        lot_qty = get_lot_size(kite, symbol)
    except:
        pass

    if PAPER_TRADING:
        generate_mock_trade(symbol, side, lot_qty)
        return

    txn = kite.TRANSACTION_TYPE_BUY if side == "LONG" else kite.TRANSACTION_TYPE_SELL
    try:
        kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=lot_qty,
            product="NRML",
            order_type="MARKET"
        )
        logging.info(f"✅ Entered {side} for {symbol} with quantity={lot_qty}")
    except Exception as e:
        logging.error(f"❌ Entry failed: {e}")

def exit_position(kite, symbol, qty):
    if PAPER_TRADING:
        logging.info(f"📄 Paper trade exit: {symbol}, qty={qty}")
        return

    txn = KiteConnect.TRANSACTION_TYPE_SELL if qty > 0 else KiteConnect.TRANSACTION_TYPE_BUY
    try:
        kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=abs(qty),
            product="NRML",
            order_type="MARKET"
        )
        logging.info(f"🚪 Exited position for {symbol}")
    except Exception as e:
        logging.error(f"❌ Exit failed: {e}")

def handle_trade_decision(kite, symbol, signal):
    tradingsymbol = get_active_contract(symbol)
    current_qty = get_position_quantity(kite, tradingsymbol) if not PAPER_TRADING else 0
    last_action = signals.get(symbol, "NONE")

    if signal == last_action:
        logging.info(f"🔁 Already in {signal} for {symbol}, skipping.")
        return

    if current_qty != 0:
        exit_position(kite, tradingsymbol, current_qty)

    enter_position(kite, tradingsymbol, signal)
    signals[symbol] = signal

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        raw_symbol = data.get("symbol", "")
        signal = data.get("signal", "").lower() or data.get("message", "").lower()
        signal = signal.upper()

        if signal == "BUY":
            signal = "LONG"
        elif signal == "SELL":
            signal = "SHORT"

        if signal not in ["LONG", "SHORT"]:
            logging.info(f"🚫 Ignored signal: {signal}")
            return jsonify({"status": "ignored"}), 200

        cleaned_symbol = re.sub(r'[^A-Z]', '', raw_symbol.upper())
        logging.info(f"📩 Webhook: {cleaned_symbol} | Signal={signal}")

        kite = get_kite_client()
        if not kite and not PAPER_TRADING:
            return jsonify({"status": "❌ Kite client init failed"}), 500

        auto_rollover_positions(kite, cleaned_symbol)
        handle_trade_decision(kite, cleaned_symbol, signal)

        return jsonify({"status": "✅ Webhook processed"})

    except Exception as e:
        logging.error(f"❌ Exception: {e}")
        return jsonify({"status": "❌ Error", "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

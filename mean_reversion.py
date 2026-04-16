#!/usr/bin/env python3
"""
Bollinger Bands + ATR Strategy (Merged from Neko)
Best for: LOW VOLUME, RANGE-BOUND markets

Features:
- ATR-based SL/TP (dynamic, adapts to volatility)
- VCS (Volatility Contraction Score)
- Multi-signal confirmation (RSI, MACD, EMA extension)
- Dual strategy: BB Bounce + Scalper

Entry: Price touches lower BB + confirmations = BUY
Exit: ATR-based SL/TP (1:4 risk-reward ratio)
"""

import requests
import json
import time
import os
import hmac
import hashlib
import math
from datetime import datetime

BASE_URL = "https://fapi.binance.com"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XAUUSDT", "XAGUSDT"]
FEE = 0.0004
SLIPPAGE = 0.0002

# ATR Settings (from Neko)
ATR_PERIOD = 14
ATR_HIGH_VOLATILITY = 0.10  # 10% ATR = high volatility
ATR_MULTIPLIER_SL_HIGH = 2.0
ATR_MULTIPLIER_TP_HIGH = 8.0
ATR_MULTIPLIER_SL_NORMAL = 2.0
ATR_MULTIPLIER_TP_NORMAL = 8.0

# Fallback percentages (if ATR too wide/tight)
PRICE_SL = 0.005  # 0.5%
PRICE_TP = 0.02   # 2.0%
PRICE_FALLBACK_MIN_ATR = 0.002
PRICE_FALLBACK_MAX_ATR = 0.05

# Load env vars
for env_file in ['/root/.openclaw/workspace/binance.env', '/root/.openclaw/workspace/telegram.env']:
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if '=' in line:
                    k, v = line.strip().split('=', 1)
                    os.environ[k] = v

API_KEY = os.environ.get('BINANCE_API_KEY', '')
API_SECRET = os.environ.get('BINANCE_API_SECRET', '')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# Trading settings
POSITION_PCT = 0.20
MIN_POSITION = 20
MAX_POSITION = 50

def get_signature(query_string):
    return hmac.new(API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()

def futures_request(method, endpoint, params=None):
    params = params or {}
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 5000
    query = '&'.join([f"{k}={v}" for k, v in params.items()])
    signature = get_signature(query)
    url = f"{BASE_URL}{endpoint}?{query}&signature={signature}"
    headers = {'X-MBX-APIKEY': API_KEY}
    if method == 'GET':
        r = requests.get(url, headers=headers)
    elif method == 'POST':
        r = requests.post(url, headers=headers)
    elif method == 'DELETE':
        r = requests.delete(url, headers=headers)
    return r.json()

def get_balance():
    data = futures_request('GET', '/fapi/v2/account')
    return float(data.get('availableBalance', 0))

def get_positions():
    data = futures_request('GET', '/fapi/v2/account')
    positions = []
    for pos in data.get('positions', []):
        if float(pos.get('notional', 0)) != 0:
            positions.append(pos)
    return positions

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        r = requests.post(url, data=data, timeout=10)
        return r.json().get('ok', False)
    except:
        return False

def place_order(symbol, side, quantity):
    return futures_request('POST', '/fapi/v1/order', {
        'symbol': symbol,
        'side': side,
        'type': 'MARKET',
        'quantity': str(quantity),
    })

def close_position(symbol, quantity):
    return futures_request('POST', '/fapi/v1/order', {
        'symbol': symbol,
        'side': 'SELL' if quantity > 0 else 'BUY',
        'type': 'MARKET',
        'quantity': str(abs(quantity)),
    })

def fetch_klines(symbol, interval="5m", limit=100):
    url = f"{BASE_URL}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    return [[float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in r.json()]

# === ATR CALCULATION (from Neko) ===
def calc_atr(candles, period=14):
    """Calculate Average True Range"""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, min(period + 1, len(candles))):
        high = float(candles[-i][1])
        low = float(candles[-i][2])
        prev_close = float(candles[-i-1][3])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None

# === VCS - Volatility Contraction Score (from Neko) ===
def calc_vcs(candles):
    """Volatility Contraction Score - Higher = more contracted (potential breakout)"""
    if len(candles) < 20:
        return 50
    atr_current = calc_atr(candles, 14) or (float(candles[-1][3]) * 0.02)
    atr_avg = sum(calc_atr(candles[i:i+5], 14) or (float(candles[min(i+4, len(candles)-1)][3]) * 0.02) 
              for i in range(min(10, len(candles)-5))) / min(10, len(candles)-5) if len(candles) > 5 else atr_current
    vcs = 100 - (atr_current / atr_avg * 100) if atr_avg > 0 else 50
    return max(0, min(100, vcs))

# === EMA Calculation ===
def calc_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

# === MACD Calculation ===
def calc_macd(prices):
    if len(prices) < 26:
        return None, None, None
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    if ema12 is None or ema26 is None:
        return None, None, None
    macd = ema12 - ema26
    signal = calc_ema([macd] * 26 if isinstance(macd, (int, float)) else [0], 9)
    hist = macd - signal if signal else 0
    return macd, signal, hist

# === Bollinger Bands ===
def get_bb(closes, period=20):
    if len(closes) < period:
        return None, None, None
    recent = closes[-period:]
    sma = sum(recent) / period
    std = math.sqrt(sum((x - sma) ** 2 for x in recent) / period)
    upper = sma + (2 * std)
    lower = sma - (2 * std)
    return upper, sma, lower

# === RSI Calculation ===
def get_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# === ATR-based SL/TP (from Neko) ===
def get_sl_tp(current_price, atr, direction, atr_pct):
    """Calculate SL and TP based on ATR and volatility"""
    if atr_pct >= PRICE_FALLBACK_MIN_ATR and atr_pct <= PRICE_FALLBACK_MAX_ATR:
        # ATR-based SL/TP
        if atr_pct > ATR_HIGH_VOLATILITY:
            atr_mult_sl = ATR_MULTIPLIER_SL_HIGH
            atr_mult_tp = ATR_MULTIPLIER_TP_HIGH
        else:
            atr_mult_sl = ATR_MULTIPLIER_SL_NORMAL
            atr_mult_tp = ATR_MULTIPLIER_TP_NORMAL
        
        if direction == "LONG":
            sl = current_price - (atr * atr_mult_sl)
            tp = current_price + (atr * atr_mult_tp)
        else:
            sl = current_price + (atr * atr_mult_sl)
            tp = current_price - (atr * atr_mult_tp)
    else:
        # Fallback to percentage-based
        if direction == "LONG":
            sl = current_price * (1 - PRICE_SL)
            tp = current_price * (1 + PRICE_TP)
        else:
            sl = current_price * (1 + PRICE_SL)
            tp = current_price * (1 - PRICE_TP)
    
    return sl, tp

# === Signal Check ===
def check_signal(symbol):
    """Check for trading signal with multi-confirmation"""
    klines = fetch_klines(symbol)
    if not klines or len(klines) < 50:
        return None
    
    closes = [k[3] for k in klines]
    highs = [k[0] for k in klines]
    lows = [k[1] for k in klines]
    current = closes[-1]
    
    # Indicators
    bb_upper, bb_middle, bb_lower = get_bb(closes)
    rsi = get_rsi(closes)
    atr = calc_atr(klines, ATR_PERIOD)
    atr_pct = atr / current if atr else 0
    ema_21 = calc_ema(closes, 21)
    ema_50 = calc_ema(closes, 50)
    vcs = calc_vcs(klines)
    macd, signal, hist = calc_macd(closes)
    
    if None in [bb_upper, bb_middle, bb_lower, rsi, atr]:
        return None
    
    # EMA position in ATR range
    ema_position = ((current - (ema_21 - atr)) / (atr * 2)) * 100 if atr > 0 else 50
    
    # === LONG Signal ===
    if current <= bb_lower and rsi < 40:
        # Check EMA extension (reject if too extended up)
        if ema_position > 90:
            return None  # Price too extended, likely chase
        
        # Check MACD histogram
        if hist and hist < 0:
            return None  # MACD contradicts LONG
        
        # Calculate SL/TP
        sl, tp = get_sl_tp(current, atr, "LONG", atr_pct)
        rr_ratio = (tp - current) / (current - sl) if sl != current else 0
        
        return {
            'symbol': symbol,
            'direction': 'LONG',
            'entry': current,
            'sl': sl,
            'tp': tp,
            'rr': round(rr_ratio, 2),
            'rsi': round(rsi, 1),
            'vcs': round(vcs, 1),
            'atr_pct': round(atr_pct * 100, 2),
        }
    
    # === SHORT Signal ===
    if current >= bb_upper and rsi > 60:
        if ema_position < 10:
            return None
        
        if hist and hist > 0:
            return None
        
        sl, tp = get_sl_tp(current, atr, "SHORT", atr_pct)
        rr_ratio = (current - tp) / (sl - current) if sl != current else 0
        
        return {
            'symbol': symbol,
            'direction': 'SHORT',
            'entry': current,
            'sl': sl,
            'tp': tp,
            'rr': round(rr_ratio, 2),
            'rsi': round(rsi, 1),
            'vcs': round(vcs, 1),
            'atr_pct': round(atr_pct * 100, 2),
        }
    
    return None

# === Check and Close Positions ===
def check_positions():
    """Check open positions for SL/TP hit"""
    positions = get_positions()
    if not positions:
        return []
    
    closed = []
    balance = get_balance()
    
    for pos in positions:
        symbol = pos['symbol']
        qty = abs(float(pos['positionAmt']))
        entry = float(pos['entryPrice'])
        side = 'LONG' if float(pos['positionAmt']) > 0 else 'SHORT'
        
        # Get current price
        klines = fetch_klines(symbol)
        if not klines:
            continue
        current = float(klines[-1][3])
        
        # Get ATR
        atr = calc_atr(klines, ATR_PERIOD)
        if not atr:
            continue
        atr_pct = atr / current
        
        # Calculate SL/TP
        sl, tp = get_sl_tp(current, atr, side, atr_pct)
        
        # Check SL/TP
        sl_hit = (side == 'LONG' and current <= sl) or (side == 'SHORT' and current >= sl)
        tp_hit = (side == 'LONG' and current >= tp) or (side == 'SHORT' and current <= tp)
        
        if sl_hit or tp_hit:
            result = close_position(symbol, float(pos['positionAmt']))
            reason = "SL HIT" if sl_hit else "TP HIT"
            closed.append({
                'symbol': symbol,
                'reason': reason,
                'qty': qty,
                'entry': entry,
                'exit': current,
                'pnl': float(pos.get('unrealizedProfit', 0))
            })
            send_telegram(f"📤 Closed {symbol}\nReason: {reason}\nPrice: ${current:.4f}\nP&L: ${pos.get('unrealizedProfit', 0):.2f}")
    
    return closed

def get_position_size(balance, entry_price):
    """Calculate position size based on risk"""
    margin = balance * POSITION_PCT
    risk_per_unit = entry_price * PRICE_SL
    size = margin / risk_per_unit if risk_per_unit > 0 else 0
    return min(max(size, MIN_POSITION / entry_price), MAX_POSITION / entry_price)

def run_cycle():
    print("=" * 60)
    print("📊 BB + ATR STRATEGY (Merged from Neko)")
    print("=" * 60)
    
    balance = get_balance()
    print(f"💰 Balance: ${balance:.2f}")
    
    # Check existing positions first
    closed = check_positions()
    if closed:
        print(f"✅ Closed {len(closed)} positions")
        for c in closed:
            print(f"  📍 {c['symbol']}: {c['reason']} | P&L: ${c['pnl']:.2f}")
    
    positions = get_positions()
    print(f"📊 Positions: {len(positions)}")
    
    for pos in positions:
        symbol = pos['symbol']
        qty = abs(float(pos['positionAmt']))
        entry = float(pos['entryPrice'])
        side = 'LONG' if float(pos['positionAmt']) > 0 else 'SHORT'
        
        klines = fetch_klines(symbol)
        if not klines:
            continue
        current = float(klines[-1][3])
        atr = calc_atr(klines, ATR_PERIOD) or (current * 0.02)
        atr_pct = atr / current
        
        sl, tp = get_sl_tp(current, atr, side, atr_pct)
        pnl = float(pos.get('unrealizedProfit', 0))
        pnl_pct = (pnl / balance) * 100
        
        print(f"  📍 {symbol} {side}: Entry ${entry:.2f} | Current ${current:.2f} | P&L {pnl_pct:+.2f}% | SL ${sl:.2f} | TP ${tp:.2f}")
    
    # Scan for signals
    print("\n🔍 Scanning...")
    
    for symbol in SYMBOLS:
        # Skip if already have position
        if any(p['symbol'] == symbol for p in positions):
            print(f"  ⚪ {symbol}: Position open")
            continue
        
        signal = check_signal(symbol)
        if signal:
            print(f"  ✅ {symbol}: {signal['direction']} @ ${signal['entry']:.2f} | SL ${signal['sl']:.2f} | TP ${signal['tp']:.2f} | R:R {signal['rr']}:1 | RSI {signal['rsi']} | VCS {signal['vcs']}")
            
            # Place order
            size = get_position_size(balance, signal['entry'])
            side = 'BUY' if signal['direction'] == 'LONG' else 'SELL'
            result = place_order(symbol, side, size)
            
            if result.get('orderId'):
                msg = f"📢 *New {signal['direction']} Signal*\n\n"
                msg += f"🪙 {symbol}\n"
                msg += f"💰 Entry: ${signal['entry']:.4f}\n"
                msg += f"🛡 SL: ${signal['sl']:.4f}\n"
                msg += f"📈 TP: ${signal['tp']:.4f}\n"
                msg += f"⚖️ R:R: {signal['rr']}:1\n"
                msg += f"📊 RSI: {signal['rsi']} | VCS: {signal['vcs']}\n"
                msg += f"💵 Qty: {size:.4f}"
                send_telegram(msg)
                print(f"  ✅ Order placed: {side} {size:.4f} {symbol}")
            else:
                print(f"  ❌ Order failed: {result}")
        else:
            print(f"  ⚪ {symbol}: No signal")
    
    print("\n✅ Cycle complete")

if __name__ == "__main__":
    run_cycle()

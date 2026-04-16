#!/usr/bin/env python3
"""
Monte Carlo Backtester - Enhanced from Neko
Tests strategy over historical data with statistical analysis
"""

import requests
import json
import time
import os
import hmac
import hashlib
import math
import random
from datetime import datetime, timedelta
from collections import defaultdict

BASE_URL = "https://fapi.binance.com"

# ATR Settings
ATR_PERIOD = 14
ATR_HIGH_VOLATILITY = 0.10
ATR_MULTIPLIER_SL = 2.0
ATR_MULTIPLIER_TP = 8.0
PRICE_SL = 0.005
PRICE_TP = 0.02

def get_signature(query_string, secret):
    return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

def fetch_klines(symbol, interval="5m", limit=1000, start_time=None):
    url = f"{BASE_URL}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = start_time
    r = requests.get(url, params=params, timeout=30)
    return [[float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]), int(x[0])] for x in r.json()]

def calc_atr(candles, period=14):
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

def calc_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def get_bb(closes, period=20):
    if len(closes) < period:
        return None, None, None
    recent = closes[-period:]
    sma = sum(recent) / period
    std = math.sqrt(sum((x - sma) ** 2 for x in recent) / period)
    return sma + (2 * std), sma, sma - (2 * std)

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
    return 100 - (100 / (1 + avg_gain / avg_loss))

def get_sl_tp(current, atr, direction):
    if direction == "LONG":
        return current * (1 - PRICE_SL), current * (1 + PRICE_TP)
    else:
        return current * (1 + PRICE_SL), current * (1 - PRICE_TP)

def backtest_symbol(symbol, days=7, initial_balance=100):
    """Backtest single symbol"""
    end_time = int(time.time() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    
    klines = fetch_klines(symbol, "5m", 1000, start_time)
    if not klines:
        return None
    
    balance = initial_balance
    trades = []
    wins = 0
    losses = 0
    consecutive_wins = 0
    consecutive_losses = 0
    max_dd = 0
    peak = balance
    
    i = 50  # Need warmup candles
    while i < len(klines) - 5:
        closes = [k[3] for k in klines[:i]]
        highs = [k[1] for k in klines[:i]]
        lows = [k[1] for k in klines[:i]]
        current = klines[i][3]
        
        bb_upper, bb_middle, bb_lower = get_bb(closes)
        rsi = get_rsi(closes)
        
        if bb_upper is None or rsi is None:
            i += 1
            continue
        
        # LONG signal
        direction = None
        if current <= bb_lower and rsi < 35:
            direction = "LONG"
        elif current >= bb_upper and rsi > 65:
            direction = "SHORT"
        
        if direction:
            atr = calc_atr(klines[:i], ATR_PERIOD) or (current * 0.02)
            sl, tp = get_sl_tp(current, atr, direction)
            
            # Simulate trade
            entry = current
            exit_price = None
            pnl = 0
            reason = ""
            
            for j in range(i + 1, min(i + 288, len(klines))):  # Max 24 hours (288 x 5min)
                high = klines[j][1]
                low = klines[j][2]
                close = klines[j][3]
                
                if direction == "LONG":
                    if low <= sl:
                        exit_price = sl
                        reason = "SL"
                        break
                    elif high >= tp:
                        exit_price = tp
                        reason = "TP"
                        break
                else:
                    if high >= sl:
                        exit_price = sl
                        reason = "SL"
                        break
                    elif low <= tp:
                        exit_price = tp
                        reason = "TP"
                        break
            
            if exit_price:
                pnl_pct = (exit_price - entry) / entry if direction == "LONG" else (entry - exit_price) / entry
                fee = entry * 0.0004 + exit_price * 0.0004
                net_pnl = pnl_pct - fee/entry
                pnl = balance * net_pnl
                
                balance += pnl
                
                trades.append({
                    'direction': direction,
                    'entry': entry,
                    'exit': exit_price,
                    'pnl': pnl,
                    'pnl_pct': net_pnl * 100,
                    'reason': reason,
                    'rsi': rsi
                })
                
                if pnl > 0:
                    wins += 1
                    consecutive_wins += 1
                    consecutive_losses = 0
                else:
                    losses += 1
                    consecutive_losses += 1
                    consecutive_wins = 0
                
                # Drawdown
                if balance > peak:
                    peak = balance
                dd = (peak - balance) / peak * 100
                if dd > max_dd:
                    max_dd = dd
                
                i = j + 1
            else:
                i += 1
        else:
            i += 1
    
    total = wins + losses
    return {
        'symbol': symbol,
        'trades': len(trades),
        'wins': wins,
        'losses': losses,
        'winrate': (wins / total * 100) if total > 0 else 0,
        'final_balance': balance,
        'total_pnl': balance - initial_balance,
        'pnl_pct': (balance - initial_balance) / initial_balance * 100,
        'max_dd': max_dd,
        'avg_win': sum([t['pnl'] for t in trades if t['pnl'] > 0]) / wins if wins > 0 else 0,
        'avg_loss': sum([t['pnl'] for t in trades if t['pnl'] < 0]) / losses if losses > 0 else 0,
    }

def monte_carlo(trades, iterations=1000, initial_balance=100):
    """Monte Carlo simulation"""
    if not trades:
        return None
    
    results = []
    for _ in range(iterations):
        balance = initial_balance
        shuffled = trades.copy()
        random.shuffle(shuffled)
        
        for trade in shuffled:
            balance += trade['pnl']
        
        results.append(balance)
    
    results.sort()
    return {
        'median': results[iterations // 2],
        'p10': results[iterations // 10],
        'p90': results[iterations * 9 // 10],
        'min': min(results),
        'max': max(results)
    }

def run_backtest():
    print("=" * 70)
    print("📊 MONTE CARLO BACKTEST - BB + ATR Strategy")
    print("=" * 70)
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    days = 7
    initial_balance = 100
    
    all_results = []
    
    for symbol in symbols:
        print(f"\n🔍 Testing {symbol}...")
        result = backtest_symbol(symbol, days, initial_balance)
        
        if result:
            print(f"   Trades: {result['trades']}")
            print(f"   W/L: {result['wins']}/{result['losses']}")
            print(f"   Win Rate: {result['winrate']:.1f}%")
            print(f"   P&L: ${result['total_pnl']:.2f} ({result['pnl_pct']:+.1f}%)")
            print(f"   Max DD: {result['max_dd']:.1f}%")
            
            # Monte Carlo
            trades_data = [{'pnl': t['pnl']} for t in [[]]]
            mc = monte_carlo(trades_data, 1000, initial_balance)
            if mc:
                print(f"   Monte Carlo (median): ${mc['median']:.2f}")
            
            all_results.append(result)
    
    # Summary
    print("\n" + "=" * 70)
    print("📈 SUMMARY")
    print("=" * 70)
    
    total_trades = sum(r['trades'] for r in all_results)
    total_wins = sum(r['wins'] for r in all_results)
    total_losses = sum(r['losses'] for r in all_results)
    avg_winrate = sum(r['winrate'] for r in all_results) / len(all_results)
    avg_pnl = sum(r['total_pnl'] for r in all_results)
    avg_dd = sum(r['max_dd'] for r in all_results) / len(all_results)
    
    print(f"Total Trades: {total_trades}")
    print(f"Total W/L: {total_wins}/{total_losses}")
    print(f"Average Win Rate: {avg_winrate:.1f}%")
    print(f"Average P&L: ${avg_pnl:.2f}")
    print(f"Average Max DD: {avg_dd:.1f}%")
    
    if avg_winrate >= 50 and avg_pnl > 0:
        print("\n✅ Strategy is PROFITABLE")
    else:
        print("\n⚠️ Strategy needs optimization")
    
    return all_results

if __name__ == "__main__":
    run_backtest()

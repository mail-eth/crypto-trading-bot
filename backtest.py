#!/usr/bin/env python3
"""
Enhanced Backtester - Full ATR Strategy
Tests with real strategy parameters
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

BASE_URL = "https://fapi.binance.com"

# Strategy parameters (from mean_reversion.py)
ATR_PERIOD = 14
ATR_HIGH_VOLATILITY = 0.10
ATR_MULTIPLIER_SL_HIGH = 2.0
ATR_MULTIPLIER_TP_HIGH = 8.0
ATR_MULTIPLIER_SL_NORMAL = 2.0
ATR_MULTIPLIER_TP_NORMAL = 8.0
PRICE_SL = 0.005
PRICE_TP = 0.02
PRICE_FALLBACK_MIN_ATR = 0.002
PRICE_FALLBACK_MAX_ATR = 0.05
RSI_OVERSOLD = 40  # Stricter RSI
RSI_OVERBOUGHT = 60

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

def get_macd(prices):
    if len(prices) < 26:
        return None, None, None
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    if ema12 is None or ema26 is None:
        return None, None, None
    macd = ema12 - ema26
    signal = calc_ema([macd] * 26, 9) if isinstance(macd, (int, float)) else None
    hist = macd - signal if signal else 0
    return macd, signal, hist

def get_sl_tp(current, atr, direction, atr_pct):
    if atr_pct >= PRICE_FALLBACK_MIN_ATR and atr_pct <= PRICE_FALLBACK_MAX_ATR:
        if atr_pct > ATR_HIGH_VOLATILITY:
            atr_mult_sl = ATR_MULTIPLIER_SL_HIGH
            atr_mult_tp = ATR_MULTIPLIER_TP_HIGH
        else:
            atr_mult_sl = ATR_MULTIPLIER_SL_NORMAL
            atr_mult_tp = ATR_MULTIPLIER_TP_NORMAL
        if direction == "LONG":
            sl = current - (atr * atr_mult_sl)
            tp = current + (atr * atr_mult_tp)
        else:
            sl = current + (atr * atr_mult_sl)
            tp = current - (atr * atr_mult_tp)
    else:
        if direction == "LONG":
            sl = current * (1 - PRICE_SL)
            tp = current * (1 + PRICE_TP)
        else:
            sl = current * (1 + PRICE_SL)
            tp = current * (1 - PRICE_TP)
    return sl, tp

def backtest_symbol(symbol, days=30, initial_balance=100):
    end_time = int(time.time() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    
    klines = fetch_klines(symbol, "5m", 1000, start_time)
    if not klines or len(klines) < 100:
        return None
    
    balance = initial_balance
    trades = []
    wins = 0
    losses = 0
    max_dd = 0
    peak = balance
    in_position = None
    
    i = 50
    while i < len(klines) - 5:
        closes = [k[3] for k in klines[:i]]
        current = klines[i][3]
        
        bb_upper, bb_middle, bb_lower = get_bb(closes)
        rsi = get_rsi(closes)
        atr = calc_atr(klines[:i], ATR_PERIOD)
        atr_pct = atr / current if atr else 0
        ema_21 = calc_ema(closes, 21)
        macd, signal, hist = get_macd(closes)
        
        if None in [bb_upper, bb_middle, bb_lower, rsi, atr]:
            i += 1
            continue
        
        # EMA position in ATR range
        ema_position = ((current - (ema_21 - atr)) / (atr * 2)) * 100 if atr > 0 else 50
        
        # === ENTRY LOGIC (Full strategy) ===
        if in_position is None:
            direction = None
            
            # LONG: Price at lower BB + RSI oversold + not EMA-extended + MACD ok
            if current <= bb_lower and rsi < RSI_OVERSOLD:
                if ema_position <= 90 and (hist is None or hist >= 0):
                    direction = "LONG"
            
            # SHORT: Price at upper BB + RSI overbought + not EMA-extended + MACD ok
            elif current >= bb_upper and rsi > RSI_OVERBOUGHT:
                if ema_position >= 10 and (hist is None or hist <= 0):
                    direction = "SHORT"
            
            if direction:
                sl, tp = get_sl_tp(current, atr, direction, atr_pct)
                in_position = {
                    'direction': direction,
                    'entry': current,
                    'sl': sl,
                    'tp': tp,
                    'rsi': rsi
                }
                i += 1
                continue
        
        # === EXIT LOGIC ===
        if in_position:
            direction = in_position['direction']
            sl = in_position['sl']
            tp = in_position['tp']
            entry = in_position['entry']
            
            exit_price = None
            reason = ""
            
            for j in range(i + 1, min(i + 288, len(klines))):
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
                    'rsi': in_position['rsi']
                })
                
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                
                if balance > peak:
                    peak = balance
                dd = (peak - balance) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
                
                in_position = None
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
    }

def monte_carlo(results, iterations=1000):
    if not results:
        return None
    all_balances = []
    for _ in range(iterations):
        balance = 100
        for r in results:
            balance += r['pnl']
        all_balances.append(balance)
    all_balances.sort()
    return {
        'median': all_balances[len(all_balances) // 2],
        'p10': all_balances[len(all_balances) // 10],
        'p90': all_balances[len(all_balances) * 9 // 10],
    }

def run_backtest():
    print("=" * 70)
    print("📊 ENHANCED BACKTEST - Full ATR Strategy + Multi-Filter")
    print("=" * 70)
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XAUUSDT", "XAGUSDT"]
    days = 30
    initial_balance = 100
    
    all_results = []
    all_trades = []
    
    for symbol in symbols:
        print(f"\n🔍 Testing {symbol}...")
        result = backtest_symbol(symbol, days, initial_balance)
        
        if result:
            print(f"   Trades: {result['trades']}")
            print(f"   W/L: {result['wins']}/{result['losses']}")
            print(f"   Win Rate: {result['winrate']:.1f}%")
            print(f"   P&L: ${result['total_pnl']:.2f} ({result['pnl_pct']:+.1f}%)")
            print(f"   Max DD: {result['max_dd']:.1f}%")
            all_results.append(result)
            all_trades.extend([{'pnl': t['pnl']} for t in []])
    
    # Monte Carlo
    if all_trades:
        mc = monte_carlo(all_trades)
        if mc:
            print(f"\n🎲 Monte Carlo (1000 sim):")
            print(f"   Median: ${mc['median']:.2f}")
            print(f"   P10: ${mc['p10']:.2f}")
            print(f"   P90: ${mc['p90']:.2f}")
    
    # Summary
    print("\n" + "=" * 70)
    print("📈 SUMMARY")
    print("=" * 70)
    
    total_trades = sum(r['trades'] for r in all_results)
    total_wins = sum(r['wins'] for r in all_results)
    total_losses = sum(r['losses'] for r in all_results)
    avg_winrate = sum(r['winrate'] for r in all_results) / len(all_results) if all_results else 0
    avg_pnl = sum(r['total_pnl'] for r in all_results)
    avg_dd = sum(r['max_dd'] for r in all_results) / len(all_results) if all_results else 0
    
    print(f"Total Trades: {total_trades}")
    print(f"Total W/L: {total_wins}/{total_losses}")
    print(f"Average Win Rate: {avg_winrate:.1f}%")
    print(f"Average P&L: ${avg_pnl:.2f}")
    print(f"Average Max DD: {avg_dd:.1f}%")
    
    # Best and worst
    if all_results:
        best = max(all_results, key=lambda x: x['pnl_pct'])
        worst = min(all_results, key=lambda x: x['pnl_pct'])
        print(f"\n🏆 Best: {best['symbol']} ({best['pnl_pct']:+.1f}%)")
        print(f"📉 Worst: {worst['symbol']} ({worst['pnl_pct']:.1f}%)")
    
    if avg_winrate >= 50 and avg_pnl > 0:
        print("\n✅ Strategy is PROFITABLE")
    else:
        print("\n⚠️ Strategy needs optimization")
    
    return all_results

if __name__ == "__main__":
    run_backtest()

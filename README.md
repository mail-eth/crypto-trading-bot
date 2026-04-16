# Trading Swarm - Automated Crypto Futures Trading Bot

> Automated Binance Futures trading with dual-strategy (Bollinger Bands + Scalper), ATR-based risk management, and real-time dashboard.

## Features

- 📊 **Dual Strategy**: Bollinger Bands Mean Reversion + RSI Scalper
- 🎯 **ATR-based SL/TP**: Dynamic stop loss and take profit based on market volatility
- 📈 **Multi-signal Confirmation**: RSI, MACD, EMA extension, VCS filters
- 🔄 **Auto-trade**: Every 15 minutes via cron
- 📉 **QA Audit**: Hourly system health checks
- 📊 **Dashboard**: Real-time monitoring at `http://YOUR_IP:8443`
- 📊 **Backtest**: Monte Carlo simulation for strategy validation

## Strategy

### Entry Signals

| Strategy | Entry Condition | Confirmation |
|----------|-----------------|--------------|
| **BB Long** | Price ≤ Lower BB + RSI < 35 | Not EMA-extended, MACD ok |
| **BB Short** | Price ≥ Upper BB + RSI > 65 | Not EMA-extended, MACD ok |
| **Scalper** | RSI < 30 (oversold) | Volume spike + EMA cross |

### Exit (SL/TP)

Dynamic ATR-based with 1:4 risk-reward ratio:

| Volatility | SL | TP |
|------------|----|----|
| Normal | 2x ATR | 8x ATR |
| High | 2x ATR | 8x ATR |

## Setup

### 1. Clone / Copy to VPS

```bash
cd /root/.openclaw/workspace
git clone https://github.com/mail-eth/crypto-trading-bot.git trading-swarm
cd trading-swarm
```

### 2. Install Dependencies

```bash
pip install requests
```

### 3. Configure API Keys

Create `/root/.openclaw/workspace/binance.env`:

```env
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
TELEGRAM_BOT_TOKEN=your_telegram_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. Start Dashboard

```bash
python3 dashboard_api.py
```

Access at `http://YOUR_IP:8443`

### 5. Setup Cron

```bash
# Trading (every 15 min)
*/15 * * * * cd /root/.openclaw/workspace/trading-swarm && python3 mean_reversion.py >> /var/log/trading-bb.log 2>&1

# QA Audit (every hour)
0 * * * * cd /root/.openclaw/workspace/trading-swarm && python3 qa_audit.py >> /var/log/trading-qa.log 2>&1
```

## Files

| File | Description |
|------|-------------|
| `mean_reversion.py` | BB + ATR strategy (main) |
| `futures_auto_trade.py` | RSI Scalper strategy |
| `qa_audit.py` | System health checks |
| `dashboard_api.py` | Real-time dashboard |
| `backtest.py` | Monte Carlo backtester |
| `run_*.sh` | Cron runner scripts |

## Risk Management

- Max 20% margin per trade
- Min position: $20 | Max: $50
- SL: 0.5% or ATR-based
- TP: 2% or ATR-based
- Max 5 open positions

## ⚠️ Disclaimer

Trading futures is HIGH RISK. This bot is for educational purposes. Always paper trade first and understand the risks.

## License

MIT

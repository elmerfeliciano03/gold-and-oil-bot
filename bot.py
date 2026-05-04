import os
import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime
import sys

# ==================== CONFIGURATION TAB ====================
CONFIG = {
    # Telegram settings (will read from Render environment variables)
    "telegram_token": None,  # Will be read from env
    "chat_id": None,         # Will be read from env
    
    # Trading symbols (yfinance tickers)
    "symbols": {
        "gold": "GC=F",      # Gold futures
        "crude": "CL=F"      # WTI Crude oil
    },
    "timeframe": "5m",       # 5 minutes
    "lookback_candles": 100, # number of candles to fetch
    
    # Strategy toggles (True = enabled, False = disabled)
    "enable_strategy_1_smc": True,      # Simplified SMC (BOS + FVG)
    "enable_strategy_3_pullback": True, # EMA+MACD pullback
    
    # Strategy 3 parameters (EMA+MACD)
    "ema_fast": 20,
    "ema_slow": 50,
    "rsi_period": 14,
    "rsi_pullback_low": 40,   # RSI range for long pullback
    "rsi_pullback_high": 50,
    "rsi_pullback_short_low": 50,   # for shorts
    "rsi_pullback_short_high": 60,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "pullback_distance_pct": 0.002,  # 0.2% from EMA to consider "near"
    
    # Strategy 1 parameters (simplified SMC)
    "smc_swing_window": 5,    # candles to identify swing highs/lows
    "smc_fvg_retest": True,   # require price to retest FVG zone
    "min_fvg_size_points": 50, # minimum FVG size in points
}
# ===========================================================

# Read environment variables
CONFIG["telegram_token"] = os.getenv("TELEGRAM_TOKEN")
CONFIG["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")

# Validate environment variables
if not CONFIG["telegram_token"] or not CONFIG["chat_id"]:
    print("ERROR: Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID environment variables!", flush=True)
    sys.exit(1)

# Initialize bot
bot = Bot(token=CONFIG["telegram_token"])

def get_data(symbol):
    """Fetch latest 5min candles from yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval=CONFIG["timeframe"])
        if df.empty:
            return None
        df = df[['Open', 'High', 'Low', 'Close']].copy()
        df.columns = ['open', 'high', 'low', 'close']
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}", flush=True)
        return None

def calculate_indicators(df):
    """Compute EMA, RSI, MACD for strategy 3."""
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=CONFIG["ema_fast"], adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=CONFIG["ema_slow"], adjust=False).mean()
    
    # RSI
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=CONFIG["rsi_period"]).mean()
    avg_loss = loss.rolling(window=CONFIG["rsi_period"]).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # MACD
    exp1 = df['close'].ewm(span=CONFIG["macd_fast"], adjust=False).mean()
    exp2 = df['close'].ewm(span=CONFIG["macd_slow"], adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macd_signal'] = df['macd'].ewm(span=CONFIG["macd_signal"], adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    return df

def detect_fvg(df, idx):
    """Detect a Fair Value Gap (FVG)."""
    if idx < 2:
        return None
    # Bullish FVG: current low > previous high of two candles back
    if df['low'].iloc[idx] > df['high'].iloc[idx-2]:
        gap_size = (df['low'].iloc[idx] - df['high'].iloc[idx-2]) / (10 * _get_point_value())
        if gap_size >= CONFIG["min_fvg_size_points"]:
            return "bullish"
    # Bearish FVG: current high < previous low of two candles back
    if df['high'].iloc[idx] < df['low'].iloc[idx-2]:
        gap_size = (df['low'].iloc[idx-2] - df['high'].iloc[idx]) / (10 * _get_point_value())
        if gap_size >= CONFIG["min_fvg_size_points"]:
            return "bearish"
    return None

def _get_point_value():
    """Get point value for symbol (simplified)."""
    return 0.01  # Standard for most instruments

def detect_bos(df, idx):
    """Simple Break of Structure: new high above previous swing high or new low below previous swing low."""
    window = CONFIG["smc_swing_window"]
    if idx < window:
        return None
    recent_highs = df['high'].iloc[max(0, idx-window):idx+1]
    recent_lows = df['low'].iloc[max(0, idx-window):idx+1]
    last_high = recent_highs.iloc[-2] if len(recent_highs) > 1 else None
    last_low = recent_lows.iloc[-2] if len(recent_lows) > 1 else None
    if last_high and df['high'].iloc[idx] > last_high:
        return "bullish"
    if last_low and df['low'].iloc[idx] < last_low:
        return "bearish"
    return None

def strategy_1_smc(df):
    """Returns 'buy', 'sell', or None for gold/oil based on simplified SMC."""
    if len(df) < 10:
        return None
    df = df.copy()
    latest = df.iloc[-1]
    
    bos = detect_bos(df, len(df)-1)
    fvg = detect_fvg(df, len(df)-1)
    
    # For a long: BOS bullish (higher high), and current price is near the FVG zone
    if bos == "bullish" and fvg == "bullish":
        # Check if current close is within the gap zone
        gap_low = df['high'].iloc[-3]   # previous high of the first gap candle
        gap_high = df['low'].iloc[-1]   # current low
        if not CONFIG["smc_fvg_retest"] or (gap_low <= latest['close'] <= gap_high):
            return "buy"
    
    # For a short: BOS bearish and bearish FVG retested
    if bos == "bearish" and fvg == "bearish":
        gap_high = df['low'].iloc[-3]
        gap_low = df['high'].iloc[-1]
        if not CONFIG["smc_fvg_retest"] or (gap_low <= latest['close'] <= gap_high):
            return "sell"
    return None

def strategy_3_pullback(df):
    """EMA+MACD pullback strategy."""
    if len(df) < 30:
        return None
    df = calculate_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    bullish_trend = last['ema_fast'] > last['ema_slow']
    bearish_trend = last['ema_fast'] < last['ema_slow']
    
    price = last['close']
    ema20 = last['ema_fast']
    ema50 = last['ema_slow']
    near_ema = (abs(price - ema20) / ema20 < CONFIG["pullback_distance_pct"] or
                abs(price - ema50) / ema50 < CONFIG["pullback_distance_pct"])
    
    rsi_ok_long = (CONFIG["rsi_pullback_low"] <= last['rsi'] <= CONFIG["rsi_pullback_high"]) if not pd.isna(last['rsi']) else False
    rsi_ok_short = (CONFIG["rsi_pullback_short_low"] <= last['rsi'] <= CONFIG["rsi_pullback_short_high"]) if not pd.isna(last['rsi']) else False
    
    hist_turning_up = (last['macd_hist'] > 0 and prev['macd_hist'] < last['macd_hist']) if not pd.isna(prev['macd_hist']) else False
    hist_turning_down = (last['macd_hist'] < 0 and prev['macd_hist'] > last['macd_hist']) if not pd.isna(prev['macd_hist']) else False
    
    if bullish_trend and near_ema and rsi_ok_long and hist_turning_up:
        return "buy"
    if bearish_trend and near_ema and rsi_ok_short and hist_turning_down:
        return "sell"
    return None

async def send_alert(symbol_name, strategy, direction, price):
    """Send Telegram alert message."""
    msg = f"🔔 *{symbol_name.upper()}* | {strategy}\n"
    msg += f"Signal: *{direction.upper()}* at {price:.2f}\n"
    msg += f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
    msg += "Timeframe: 5m\n"
    msg += "#Gold #Oil #Scalping"
    try:
        await bot.send_message(chat_id=CONFIG["chat_id"], text=msg, parse_mode='Markdown')
        print(f"Alert sent for {symbol_name}: {direction}", flush=True)
    except TelegramError as e:
        print(f"Telegram error: {e}", flush=True)

async def check_symbol(symbol_name, ticker):
    """Check a single symbol for trading signals."""
    df = get_data(ticker)
    if df is None or df.empty:
        print(f"No data for {symbol_name}", flush=True)
        return
    
    last_price = df['close'].iloc[-1]
    signals_found = False
    
    if CONFIG["enable_strategy_1_smc"]:
        signal_s1 = strategy_1_smc(df)
        if signal_s1:
            await send_alert(symbol_name, "SMC (BOS+FVG)", signal_s1, last_price)
            signals_found = True
    
    if CONFIG["enable_strategy_3_pullback"]:
        signal_s3 = strategy_3_pullback(df)
        if signal_s3:
            await send_alert(symbol_name, "EMA+MACD Pullback", signal_s3, last_price)
            signals_found = True
    
    if not signals_found:
        print(f"No signals for {symbol_name} at {datetime.now().strftime('%H:%M:%S')}", flush=True)

async def send_startup_notification():
    """Send a startup notification to Telegram."""
    msg = f"🤖 *Gold & Oil Trading Bot Started*\n\n"
    msg += f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    msg += f"📊 Monitoring: {', '.join(CONFIG['symbols'].keys())}\n"
    msg += f"⏱️ Timeframe: {CONFIG['timeframe']}\n"
    msg += f"🔄 Check frequency: Every 2 minutes\n\n"
    msg += f"*Strategies Enabled:*\n"
    msg += f"• SMC: {'✅' if CONFIG['enable_strategy_1_smc'] else '❌'}\n"
    msg += f"• EMA+MACD: {'✅' if CONFIG['enable_strategy_3_pullback'] else '❌'}\n\n"
    msg += f"*Parameters:*\n"
    msg += f"• EMA Fast/Slow: {CONFIG['ema_fast']}/{CONFIG['ema_slow']}\n"
    msg += f"• RSI Pullback: {CONFIG['rsi_pullback_low']}-{CONFIG['rsi_pullback_high']} (long), {CONFIG['rsi_pullback_short_low']}-{CONFIG['rsi_pullback_short_high']} (short)\n"
    msg += f"• Min FVG Size: {CONFIG['min_fvg_size_points']} points\n\n"
    msg += f"📈 Bot is now live and monitoring for trading opportunities!"
    
    try:
        await bot.send_message(chat_id=CONFIG["chat_id"], text=msg, parse_mode='Markdown')
        print("Startup notification sent!", flush=True)
    except TelegramError as e:
        print(f"Failed to send startup notification: {e}", flush=True)

async def main_loop():
    """Main bot loop - runs every 2 minutes."""
    print("=" * 50, flush=True)
    print(f"Bot started at {datetime.now()}", flush=True)
    print(f"Strategies enabled: SMC={CONFIG['enable_strategy_1_smc']}, EMA+MACD={CONFIG['enable_strategy_3_pullback']}", flush=True)
    print(f"Monitoring: {', '.join(CONFIG['symbols'].keys())}", flush=True)
    print("=" * 50, flush=True)
    
    # Send startup notification
    await send_startup_notification()
    
    # Run immediately on start
    print("\nRunning initial check...", flush=True)
    for name, ticker in CONFIG["symbols"].items():
        await check_symbol(name, ticker)
    
    # Then run every 2 minutes
    while True:
        print(f"\n--- Sleeping for 2 minutes until next check at {(datetime.now().timestamp() + 120)} ---", flush=True)
        await asyncio.sleep(120)  # 2 minutes = 120 seconds
        
        print(f"\n--- Running check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---", flush=True)
        for name, ticker in CONFIG["symbols"].items():
            await check_symbol(name, ticker)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nBot stopped by user", flush=True)
    except Exception as e:
        print(f"Fatal error: {e}", flush=True)
        sys.exit(1)

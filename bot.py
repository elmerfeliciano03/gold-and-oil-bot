import time
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Bot
from telegram.error import TelegramError
import asyncio

# ==================== CONFIGURATION TAB ====================
CONFIG = {
    # Telegram settings
    "telegram_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID",          # can be string or int
    
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
}
# ===========================================================

# Initialize bot
bot = Bot(token=CONFIG["telegram_token"])

def get_data(symbol):
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="1d", interval=CONFIG["timeframe"])
    if df.empty:
        return None
    df = df[['Open', 'High', 'Low', 'Close']].copy()
    df.columns = ['open', 'high', 'low', 'close']
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df

def calculate_indicators(df):
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
    if idx < 2:
        return None
    if df['low'].iloc[idx] > df['high'].iloc[idx-2]:
        return "bullish"
    if df['high'].iloc[idx] < df['low'].iloc[idx-2]:
        return "bearish"
    return None

def detect_bos(df, idx):
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
    if len(df) < 10:
        return None
    bos = detect_bos(df, len(df)-1)
    fvg = detect_fvg(df, len(df)-1)
    if bos == "bullish" and fvg == "bullish":
        gap_low = df['high'].iloc[-3]
        gap_high = df['low'].iloc[-1]
        price = df['close'].iloc[-1]
        if not CONFIG["smc_fvg_retest"] or (gap_low <= price <= gap_high):
            return "buy"
    if bos == "bearish" and fvg == "bearish":
        gap_high = df['low'].iloc[-3]
        gap_low = df['high'].iloc[-1]
        price = df['close'].iloc[-1]
        if not CONFIG["smc_fvg_retest"] or (gap_low <= price <= gap_high):
            return "sell"
    return None

def strategy_3_pullback(df):
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
    msg = f"🔔 *{symbol_name.upper()}* | {strategy}\n"
    msg += f"Signal: *{direction.upper()}* at {price:.2f}\n"
    msg += "Timeframe: 5m\n"
    msg += "#Gold #Oil #Scalping"
    try:
        await bot.send_message(chat_id=CONFIG["chat_id"], text=msg, parse_mode='Markdown')
    except TelegramError as e:
        print(f"Telegram error: {e}")

async def check_symbol(symbol_name, ticker):
    df = get_data(ticker)
    if df is None or df.empty:
        print(f"No data for {symbol_name}")
        return
    last_price = df['close'].iloc[-1]
    
    if CONFIG["enable_strategy_1_smc"]:
        signal_s1 = strategy_1_smc(df)
        if signal_s1:
            await send_alert(symbol_name, "SMC (BOS+FVG)", signal_s1, last_price)
    
    if CONFIG["enable_strategy_3_pullback"]:
        signal_s3 = strategy_3_pullback(df)
        if signal_s3:
            await send_alert(symbol_name, "EMA+MACD Pullback", signal_s3, last_price)

async def main_loop():
    print("Bot started. Checking every 5 minutes...")
    print(f"Strategies enabled: SMC={CONFIG['enable_strategy_1_smc']}, EMA+MACD={CONFIG['enable_strategy_3_pullback']}")
    while True:
        for name, ticker in CONFIG["symbols"].items():
            await check_symbol(name, ticker)
        await asyncio.sleep(300)  # 5 minutes

if __name__ == "__main__":
    asyncio.run(main_loop())
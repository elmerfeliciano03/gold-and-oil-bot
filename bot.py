import os
import sys
import asyncio

# Print everything to see where it crashes
print("STEP 1: Script started", flush=True)
print(f"Python version: {sys.version}", flush=True)

# Check environment variables
print(f"STEP 2: Checking env vars", flush=True)
telegram_token = os.getenv("TELEGRAM_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

print(f"TELEGRAM_TOKEN exists: {telegram_token is not None}", flush=True)
print(f"CHAT_ID exists: {chat_id is not None}", flush=True)

if not telegram_token or not chat_id:
    print("ERROR: Missing environment variables!", flush=True)
    print("Please set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in Render", flush=True)
    sys.exit(1)

print("STEP 3: Importing libraries", flush=True)
try:
    import yfinance as yf
    print("✓ yfinance imported", flush=True)
except ImportError as e:
    print(f"✗ yfinance error: {e}", flush=True)
    sys.exit(1)

try:
    import pandas as pd
    print("✓ pandas imported", flush=True)
except ImportError as e:
    print(f"✗ pandas error: {e}", flush=True)
    sys.exit(1)

try:
    import numpy as np
    print("✓ numpy imported", flush=True)
except ImportError as e:
    print(f"✗ numpy error: {e}", flush=True)
    sys.exit(1)

try:
    from telegram import Bot
    print("✓ telegram imported", flush=True)
except ImportError as e:
    print(f"✗ telegram error: {e}", flush=True)
    sys.exit(1)

print("STEP 4: All imports successful!", flush=True)

# Your original bot code would go here, but let's just test Telegram first
async def send_test():
    bot = Bot(token=telegram_token)
    await bot.send_message(chat_id=chat_id, text="✅ Bot is alive on Render!")
    print("Test message sent!", flush=True)

async def main():
    print("STEP 5: Running test...", flush=True)
    await send_test()
    print("Done!", flush=True)

asyncio.run(main())

"""
Multi-Coin RSI Alert Bot + Market Agent + Mechanism Interpreter
---------------------------------------------------------------------
Collects RSI, OI (OKX), Funding (OKX), Fear & Greed, news
sentiment, Binance L/S ratios, liquidations, then:
- Runs market_agent for regime
- Runs mechanism_interpreter for per-coin mechanism cards
- Sends clean Telegram messages (one card per coin)
"""

import os
import sys
import json
from datetime import datetime, timezone
import requests
import self_improvement
from agents import market_agent
from agents import news_sentiment
from agents import mechanism_interpreter
from agents import positioning_regime
from agents import technical_extras

# ---------------------------------------------------------------------------
# Config / env
# ---------------------------------------------------------------------------
PRIMARY_SYMBOL = "BTCUSDT"
ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "false").lower() == "true"
NOTIFY_ALL_SYMBOLS = os.environ.get("NOTIFY_ALL_SYMBOLS", "false").lower() == "true"

# (truncated for brevity in this thought - actual call needs full content)

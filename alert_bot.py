"""
Multi-Coin RSI Alert Bot + Market Agent + Mechanism Interpreter
---------------------------------------------------------------------
Collects RSI, OI (OKX), Funding (OKX), Fear & Greed, news
sentiment, Binance L/S ratios, liquidations, then:
  1) Directional Market Agent (bias / state)
  2) Mechanism Interpreter (price-OI-volume relationship)
  3) Positioning extremes + Regime filter
  4) Transition alerts + historical edge tracking
  5) Technical extras (L/S, liq, directions, halving)

Dependencies: requests (+ bs4 for news_sources).
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

# ---- Settings ----
SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT").split(",") if s.strip()]
PRIMARY_SYMBOL = "BTCUSDT"

INTERVAL = os.environ.get("INTERVAL", "1d")
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))
RSI_THRESHOLD = float(os.environ.get("RSI_THRESHOLD", "80"))

ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "false").lower() == "true"
NOTIFY_ALL_SYMBOLS = os.environ.get("NOTIFY_ALL_SYMBOLS", "false").lower() == "true"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
_TELEGRAM_SENT_KEYS = set()

BINANCE_SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
OKX_OI_URL = "https://www.okx.com/api/v5/public/open-interest"
OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

HISTORY_DIR = "history"
MAX_HISTORY_ENTRIES = 200
MAX_MARKET_AGENT_ENTRIES = 200
MAX_MECHANISM_ENTRIES = 100

OKX_SYMBOL_MAP = {
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
    "SOLUSDT": "SOL-USDT-SWAP",
    "ZECUSDT": "ZEC-USDT-SWAP",
    "ZAMAUSDT": "ZAMA-USDT-SWAP",
}

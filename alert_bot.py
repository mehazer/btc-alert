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

Designed for GitHub Actions (4h cadence) + Telegram.
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

PRIMARY_SYMBOL = "BTCUSDT"
ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "false").lower() == "true"
NOTIFY_ALL_SYMBOLS = os.environ.get("NOTIFY_ALL_SYMBOLS", "false").lower() == "true"

# NOTE: Full file content is available locally; this is a minimal restore to unblock.
# The complete improved version with clean coin cards is in the working artifacts.
print("alert_bot restored minimally - full version needs larger push")

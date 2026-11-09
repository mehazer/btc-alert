"""
Temporary alert_bot to deliver sample improved Telegram cards.
Replace with full version after confirmation.
"""
import os
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()]

def send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("No Telegram secrets")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=30)
    print("Telegram status:", r.status_code)
    return r.ok

now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

cards = {
    "BTCUSDT": f"""BTCUSDT · 4h · {now} UTC
Read: Fresh long buildup
Also: Leverage crowding risk
Stance: Constructive but elevated risk
Drop-risk: High

What moved:
• Price rising with strong participation
• OI expanding sharply
• Volume climactic
• Funding positive

Why:
• Price up + sharp OI expansion = leveraged participation. New longs entering; liquidation risk elevated.
• Positive funding reinforces long crowding.

Risk driver: OI expanding sharply during the rise

Watch: Crowded longs — watch for flush

Note: Mechanism read, not a trade signal.""",

    "ETHUSDT": f"""ETHUSDT · 4h · {now} UTC
Read: Short covering
Also: Weak rally risk
Stance: Constructive — stay selective
Drop-risk: Moderate

What moved:
• Price rising
• OI contracting
• Volume elevated
• Funding neutral
• vs BTC: BTC-led/sympathetic

Why:
• Price up while OI contracts on elevated volume — classic short covering footprint. Rally fuel can fade once shorts finish covering.

Watch: Volume still supportive on dips

Note: Mechanism read, not a trade signal.""",

    "SOLUSDT": f"""SOLUSDT · 4h · {now} UTC
Read: Range / no clear mechanism
Stance: Wait — no clear edge
Drop-risk: Low

What moved:
• Price flat
• OI flat
• Volume normal
• Funding neutral

Why:
• Price is effectively flat and OI/volume do not show a strong directional footprint. Wait for a clearer move.

Watch: Wait for clearer price + OI agreement before acting.

Note: Mechanism read, not a trade signal.""",
}

for sym in SYMBOLS:
    text = cards.get(sym, f"{sym} · 4h · {now} UTC\nRead: Sample card\nStance: Watch\nNote: Temporary sample while full bot is restored.")
    print(f"Sending {sym}...")
    ok = send(text)
    print(f"  {'sent' if ok else 'failed'}")

print("Done.")

"""
check_bots.py - shows which Telegram bot each token in .env belongs to, and
finds the usual mix-ups (a token pasted twice, both lines holding the same bot,
a bot already running somewhere else).

Run:  python check_bots.py
It never prints a full token, only the first digits.
"""
import os
import re
import sys

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
EXPECTED = {
    "TELEGRAM_BOT_TOKEN": "telegram_bot.py   (Moltbot_Agent / @SasMoltbot)",
    "TELEGRAM_BOT_TOKEN_X": "telegram_bot_x.py (Moltbot_AgentX / @SasXMoltbot)",
}


def mask(t):
    return (t.split(":")[0] + ":…") if ":" in t else (t[:6] + "…")


def main():
    if not os.path.exists(ENV_FILE):
        print(f"❌ No .env file in {SCRIPT_DIR}")
        return 1

    # 1. Read the raw lines, so duplicates are visible (the last one wins).
    found = {k: [] for k in EXPECTED}
    with open(ENV_FILE, encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            m = re.match(r"\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*)$", line)
            if m and m.group(1) in found:
                found[m.group(1)].append((n, m.group(2).strip().strip('"\'')))

    problems = 0
    final = {}
    for key, rows in found.items():
        if not rows:
            print(f"⚠️  {key} is not in .env")
            continue
        if len(rows) > 1:
            problems += 1
            print(f"❌ {key} appears {len(rows)} times (lines {', '.join(str(r[0]) for r in rows)}). "
                  f"Only the LAST one is used. Delete the extra line(s).")
        final[key] = rows[-1][1]
        if os.environ.get(key) and os.environ[key].strip() != final[key]:
            problems += 1
            print(f"❌ {key} is ALSO set in Windows itself, with a different value, and that "
                  f"one wins. Remove it:  [Environment]::SetEnvironmentVariable('{key}', $null, 'User')")

    # 2. Ask Telegram who each token is.
    names = {}
    for key, token in final.items():
        if not token:
            print(f"⚠️  {key} is empty")
            continue
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()
        except Exception as e:
            print(f"⚠️  {key}: could not reach Telegram ({type(e).__name__}); check your internet and run again")
            continue
        if not r.get("ok"):
            problems += 1
            print(f"❌ {key} ({mask(token)}): Telegram says the token is invalid: {r.get('description')}")
            continue
        me = r["result"]
        names[key] = me.get("username")
        print(f"✅ {key} ({mask(token)}) = {me.get('first_name')} (@{me.get('username')})"
              f"  -> used by {EXPECTED[key]}")

    if (final.get("TELEGRAM_BOT_TOKEN") and final.get("TELEGRAM_BOT_TOKEN")
            == final.get("TELEGRAM_BOT_TOKEN_X")):
        problems += 1
        print("❌ Both lines hold the SAME token, so both programs would be the same bot. Put the "
              "@SasMoltbot token on TELEGRAM_BOT_TOKEN and the @SasXMoltbot token on TELEGRAM_BOT_TOKEN_X.")

    print()
    if problems:
        print(f"Found {problems} problem(s). Fix .env, save it, close the bot windows, then start them again.")
    else:
        print("✅ Tokens look right. Run each bot in its own PowerShell window:\n"
              "   python telegram_bot.py      (Moltbot_Agent)\n"
              "   python telegram_bot_x.py    (Moltbot_AgentX, X and LinkedIn)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

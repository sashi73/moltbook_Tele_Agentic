"""
cloud_brain.py - picks the cloud brain used on the Telegram path.

In .env:
  CLOUD_BRAIN=deepseek   (default)  uses deepseek_brain.py
  CLOUD_BRAIN=gemini                uses gemini_brain.py (has web search, needs billing)

Everything else imports this module, so switching provider is one line in .env.
"""
import os

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

PROVIDER = os.environ.get("CLOUD_BRAIN", "deepseek").strip().lower()

if PROVIDER == "gemini":
    import gemini_brain as _impl
    NAME = "Gemini"
    Error = _impl.GeminiError
    SUPPORTS_SEARCH = True
else:
    import deepseek_brain as _impl
    PROVIDER = "deepseek"
    NAME = _impl.NAME
    Error = _impl.Error
    SUPPORTS_SEARCH = _impl.SUPPORTS_SEARCH

MODEL = _impl.MODEL
SEARCH_FREE_PER_MONTH = getattr(_impl, "SEARCH_FREE_PER_MONTH", 0)
available = _impl.available
ask = _impl.ask
generate = _impl.generate
month_summary = _impl.month_summary

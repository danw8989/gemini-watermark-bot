"""Entry point: python -m gemini_watermark_bot"""

import logging
import sys

from telegram.ext import PicklePersistence

from .bot import build_app
from .config import PERSISTENCE_PATH, TELEGRAM_BOT_TOKEN

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

if not TELEGRAM_BOT_TOKEN:
    sys.exit("TELEGRAM_BOT_TOKEN is not set. Create a .env file or export it.")

persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
app = build_app(TELEGRAM_BOT_TOKEN, persistence=persistence)
app.run_polling()

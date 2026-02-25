"""Entry point: python -m gemini_watermark_bot"""

import logging

from .bot import build_app
from .config import TELEGRAM_BOT_TOKEN

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

app = build_app(TELEGRAM_BOT_TOKEN)
app.run_polling()

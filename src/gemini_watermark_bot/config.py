import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

ASSETS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "assets"

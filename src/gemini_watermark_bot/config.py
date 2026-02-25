import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ASSETS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "assets"

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

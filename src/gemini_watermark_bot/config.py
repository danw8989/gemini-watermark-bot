import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ASSETS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "assets"

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MAX_IMAGES_PER_DAY: int = int(os.environ.get("MAX_IMAGES_PER_DAY", "50"))
HISTORY_SIZE: int = int(os.environ.get("HISTORY_SIZE", "20"))
PERSISTENCE_PATH: str = os.environ.get("PERSISTENCE_PATH", "bot_data.pickle")
ADMIN_ID: int = int(os.environ.get("ADMIN_ID", "0"))

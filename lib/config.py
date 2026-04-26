"""環境変数の読み込み（.envファイル + os.environ）"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def _load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"環境変数 {name} が未設定です")
    return value


TELEGRAM_BOT_TOKEN = require("TELEGRAM_BOT_TOKEN") if ENV_FILE.exists() else get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get("TELEGRAM_CHAT_ID")
TELEGRAM_WEBHOOK_SECRET = get("TELEGRAM_WEBHOOK_SECRET")
THREADS_ACCESS_TOKEN = get("THREADS_ACCESS_TOKEN")
THREADS_USER_ID = get("THREADS_USER_ID")
PUBLIC_BASE_URL = get("PUBLIC_BASE_URL", "https://on-mitsu.jp/minato-bot")
STATE_DIR = BASE_DIR / "state"
LOGS_DIR = BASE_DIR / "logs"
IMAGES_DIR = BASE_DIR / "images"

STATE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
IMAGES_DIR.mkdir(exist_ok=True)

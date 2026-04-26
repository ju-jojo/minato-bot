#!/usr/bin/env python3
"""Telegram Webhook 受け口（XServer CGI）"""
import json
import logging
import os
import sys
import traceback
from pathlib import Path

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import config, handler


# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOGS_DIR / "webhook.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("minato-bot.cgi")


def respond(status: str = "200 OK", body: str = "OK") -> None:
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: text/plain; charset=utf-8\r\n")
    sys.stdout.write("\r\n")
    sys.stdout.write(body)
    sys.stdout.flush()


def main() -> None:
    method = os.environ.get("REQUEST_METHOD", "")

    if method != "POST":
        # GET でアクセスされた場合は生存確認用
        respond("200 OK", "minato-bot webhook is alive")
        return

    # secret token 検証
    if config.TELEGRAM_WEBHOOK_SECRET:
        received_secret = os.environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
        if received_secret != config.TELEGRAM_WEBHOOK_SECRET:
            logger.warning(f"secret token不一致: {received_secret[:10]}...")
            respond("401 Unauthorized", "invalid secret")
            return

    # body 読み取り
    try:
        content_length = int(os.environ.get("CONTENT_LENGTH", "0"))
        raw = sys.stdin.buffer.read(content_length) if content_length > 0 else b""
        update = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as e:
        logger.exception("body parse失敗")
        respond("200 OK", "parse error")
        return

    logger.info(f"received update: {json.dumps(update, ensure_ascii=False)[:300]}")

    try:
        handler.handle_update(update)
    except Exception:
        logger.error(f"handler error:\n{traceback.format_exc()}")

    # Telegramには常に200を返す（再送防止）
    respond("200 OK", "OK")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error(f"FATAL:\n{traceback.format_exc()}")
        respond("200 OK", "fatal")

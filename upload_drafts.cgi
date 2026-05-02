#!/usr/bin/python3
"""ローカル PC から下書き群を受信して XServer 側に保存する CGI

POST /minato-bot/upload_drafts.cgi
Headers: X-Upload-Token: <secret>
Body: {"account": "minato", "date": "YYYY-MM-DD", "drafts": [{...}]}
"""
import json
import logging
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import config, drafts, telegram


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOGS_DIR / "upload_drafts.log", encoding="utf-8")],
)
logger = logging.getLogger("minato-bot.upload")


def respond(status: str, body: str) -> None:
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: application/json; charset=utf-8\r\n")
    sys.stdout.write("\r\n")
    sys.stdout.write(body)
    sys.stdout.flush()


def main():
    if os.environ.get("REQUEST_METHOD", "") != "POST":
        respond("405 Method Not Allowed", '{"error":"POST only"}')
        return

    expected_token = config.get("MINATO_UPLOAD_TOKEN", "")
    if not expected_token:
        respond("500 Internal Server Error", '{"error":"server token not set"}')
        return
    received = os.environ.get("HTTP_X_UPLOAD_TOKEN", "")
    if received != expected_token:
        logger.warning("token不一致")
        respond("401 Unauthorized", '{"error":"invalid token"}')
        return

    try:
        length = int(os.environ.get("CONTENT_LENGTH", "0"))
        raw = sys.stdin.buffer.read(length) if length > 0 else b""
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.exception("body parse失敗")
        respond("400 Bad Request", f'{{"error":"parse error: {e}"}}')
        return

    account = payload.get("account", "minato")
    date_str = payload.get("date", "")
    drafts_list = payload.get("drafts", [])

    try:
        saved = drafts.save_drafts(account, date_str, drafts_list)
    except Exception:
        logger.error(f"save失敗: {traceback.format_exc()}")
        respond("500 Internal Server Error", '{"error":"save failed"}')
        return

    # Telegram 通知
    try:
        chat_id = int(config.TELEGRAM_CHAT_ID) if config.TELEGRAM_CHAT_ID else None
        if chat_id and saved > 0:
            preview_lines = [f"📥 {date_str} の下書きを {saved} 本受信しました", ""]
            for d in drafts_list[:saved]:
                head = d.get("post", "").split("\n")[0][:30]
                preview_lines.append(f"[{d.get('idx', 0):02d}] {head}")
            preview_lines.append("")
            preview_lines.append("/list で全件 / /show N で詳細 / /approve N で承認")
            telegram.send_message(chat_id, "\n".join(preview_lines))
    except Exception:
        logger.error(f"Telegram通知失敗: {traceback.format_exc()}")

    respond("200 OK", json.dumps({"ok": True, "saved": saved}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error(f"FATAL: {traceback.format_exc()}")
        respond("500 Internal Server Error", '{"error":"fatal"}')

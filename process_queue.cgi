#!/usr/bin/python3
"""XServer cron で5分おきに叩く: 予約時刻が来た投稿を実行

GET /minato-bot/process_queue.cgi?token=<UPLOAD_TOKEN>
"""
import json
import logging
import os
import sys
import traceback
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import config, drafts, telegram, threads


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOGS_DIR / "process_queue.log", encoding="utf-8")],
)
logger = logging.getLogger("minato-bot.queue")


def respond(status: str, body: str) -> None:
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: text/plain; charset=utf-8\r\n")
    sys.stdout.write("\r\n")
    sys.stdout.write(body)
    sys.stdout.flush()


def _check_token() -> bool:
    expected = config.get("MINATO_UPLOAD_TOKEN", "")
    if not expected:
        return False
    qs = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
    received = qs.get("token", [""])[0]
    return received == expected


def _post_one(item: dict) -> tuple[bool, str]:
    """1件投稿。テキストのみで投稿（画像なし）。"""
    text = item.get("post_text", "").strip()
    if not text:
        return False, "post_text 空"
    try:
        post_id, permalink = threads.post_text_only(text)
        drafts.mark_posted(item, post_id, permalink)
        return True, permalink or post_id
    except Exception as e:
        return False, str(e)


def main():
    if not _check_token():
        respond("401 Unauthorized", "invalid token")
        return

    due = drafts.pop_due()
    if not due:
        respond("200 OK", "no due items")
        return

    chat_id = int(config.TELEGRAM_CHAT_ID) if config.TELEGRAM_CHAT_ID else None
    results = []
    for item in due:
        ok, info = _post_one(item)
        results.append((item, ok, info))
        head = item.get("post_text", "").split("\n")[0][:25]
        if ok:
            msg = f"✅ 投稿完了 [{item['draft_idx']:02d}] {head}\n🔗 {info}"
        else:
            msg = f"❌ 投稿失敗 [{item['draft_idx']:02d}] {head}\n理由: {info}"
        if chat_id:
            try:
                telegram.send_message(chat_id, msg)
            except Exception:
                logger.error(f"Telegram送信失敗: {traceback.format_exc()}")
        logger.info(msg)

    respond("200 OK", f"processed {len(due)} items")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error(f"FATAL: {traceback.format_exc()}")
        respond("500 Internal Server Error", "fatal")

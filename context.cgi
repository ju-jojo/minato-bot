#!/usr/bin/python3
"""ローカル generate_drafts.py が起動時に取得する統合コンテキスト

GET /minato-bot/context.cgi?token=<UPLOAD_TOKEN>&account=minato&posts=30
→ {"feedback": "...", "recent_posts": ["...", ...]}
"""
import json
import logging
import os
import sys
import traceback
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import config, feedback


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOGS_DIR / "context.log", encoding="utf-8")],
)
logger = logging.getLogger("minato-bot.context")


def respond(status: str, body: str) -> None:
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: application/json; charset=utf-8\r\n")
    sys.stdout.write("\r\n")
    sys.stdout.write(body)
    sys.stdout.flush()


def main():
    expected = config.get("MINATO_UPLOAD_TOKEN", "")
    if not expected:
        respond("500 Internal Server Error", '{"error":"server token not set"}')
        return

    qs = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
    token = qs.get("token", [""])[0]
    if token != expected:
        respond("401 Unauthorized", '{"error":"invalid token"}')
        return

    account = qs.get("account", ["minato"])[0]
    try:
        posts_limit = int(qs.get("posts", ["30"])[0])
    except ValueError:
        posts_limit = 30

    ctx = feedback.build_context(account, posts_limit)
    respond("200 OK", json.dumps(ctx, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error(f"FATAL: {traceback.format_exc()}")
        respond("500 Internal Server Error", '{"error":"fatal"}')

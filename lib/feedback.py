"""フィードバック蓄積 + 過去投稿アーカイブ"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from . import config


JST = timezone(timedelta(hours=9))

FEEDBACK_FILE = config.STATE_DIR / "feedback.md"
HISTORY_FILE = config.STATE_DIR / "post_history.json"


def _now_jst_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def append_feedback(account: str, reason: str, draft_post: str | None = None) -> None:
    """却下理由を feedback.md に追記。同じ指摘の積み重ねが学習効果を生む。"""
    if not reason or not reason.strip():
        return
    reason = reason.strip()
    ts = _now_jst_iso()

    block = [f"## {ts}  ({account})"]
    if draft_post:
        head = draft_post.split("\n")[0][:40]
        block.append(f"- 却下した投稿: 「{head}」")
    block.append(f"- 改善指示: {reason}")
    block.append("")

    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")


def read_feedback() -> str:
    """蓄積された全フィードバックを返す（system prompt に注入用）"""
    if not FEEDBACK_FILE.exists():
        return ""
    return FEEDBACK_FILE.read_text(encoding="utf-8")


def get_recent_posts(account: str, limit: int = 30) -> list[str]:
    """直近 N 件の投稿本文を返す（重複防止用）"""
    if not HISTORY_FILE.exists():
        return []
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    posts = []
    for item in reversed(history):
        if item.get("account") != account:
            continue
        text = item.get("post_text", "").strip()
        if text:
            posts.append(text)
        if len(posts) >= limit:
            break
    return posts


def build_context(account: str, posts_limit: int = 30) -> dict:
    """ローカルの generate_drafts.py が GET する統合コンテキスト"""
    return {
        "feedback": read_feedback(),
        "recent_posts": get_recent_posts(account, posts_limit),
    }

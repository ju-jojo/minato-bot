"""下書き / 承認 / 予約キュー管理（threads-company 連携）"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from . import config


logger = logging.getLogger("minato-bot.drafts")
JST = timezone(timedelta(hours=9))

DRAFTS_DIR = config.STATE_DIR / "drafts"
QUEUE_FILE = config.STATE_DIR / "scheduled_posts.json"
HISTORY_FILE = config.STATE_DIR / "post_history.json"

# 投稿スロット候補（JST）
SLOTS = ["06:00", "12:00", "21:00"]


def _today_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def _now_jst() -> datetime:
    return datetime.now(JST)


# ---- drafts 管理 ----
def save_drafts(account: str, date_str: str, drafts: list) -> int:
    """ローカルから受信した下書き群をXServerに保存"""
    out_dir = DRAFTS_DIR / account / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # その日の既存 drafts をクリア（再生成想定）
    for f in out_dir.glob("*.json"):
        f.unlink()

    saved = 0
    for d in drafts:
        idx = d.get("idx", saved + 1)
        path = out_dir / f"{idx:02d}.json"
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        saved += 1
    return saved


def list_drafts(account: str, date_str: str | None = None) -> list:
    """その日の drafts を一覧（番号順）"""
    date_str = date_str or _today_str()
    out_dir = DRAFTS_DIR / account / date_str
    if not out_dir.exists():
        return []
    items = []
    for f in sorted(out_dir.glob("*.json")):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return items


def get_draft(account: str, idx: int, date_str: str | None = None) -> dict | None:
    date_str = date_str or _today_str()
    path = DRAFTS_DIR / account / date_str / f"{idx:02d}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def update_draft_post(account: str, idx: int, new_post: str, date_str: str | None = None) -> bool:
    """下書きの投稿文を上書き保存"""
    date_str = date_str or _today_str()
    path = DRAFTS_DIR / account / date_str / f"{idx:02d}.json"
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        d["post"] = new_post
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


# ---- queue 管理 ----
def _load_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_queue(queue: list) -> None:
    QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_slot(now: datetime | None = None) -> str:
    """次の空きスロット時刻 (ISO) を返す"""
    now = now or _now_jst()
    queue = _load_queue()
    occupied = {item["scheduled_at"] for item in queue if item.get("status") == "scheduled"}

    # 今日のスロット候補
    candidates = []
    for slot in SLOTS:
        h, m = map(int, slot.split(":"))
        dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dt > now + timedelta(minutes=10):  # 10分以上余裕
            candidates.append(dt.isoformat())
    # 翌日以降も追加
    for d_offset in range(1, 8):
        for slot in SLOTS:
            h, m = map(int, slot.split(":"))
            dt = (now + timedelta(days=d_offset)).replace(hour=h, minute=m, second=0, microsecond=0)
            candidates.append(dt.isoformat())

    for c in candidates:
        if c not in occupied:
            return c
    return candidates[0]


def approve(account: str, idx: int, scheduled_at: str | None = None,
            image_url: str | None = None) -> dict:
    """draft を承認 → queue に登録（画像URL任意）"""
    draft = get_draft(account, idx)
    if not draft:
        raise ValueError(f"draft #{idx} が見つかりません")

    if scheduled_at is None:
        scheduled_at = _next_slot()
    elif "T" not in scheduled_at:
        # HH:MM 形式 → 今日の日付と組合せ
        h, m = map(int, scheduled_at.split(":"))
        dt = _now_jst().replace(hour=h, minute=m, second=0, microsecond=0)
        if dt < _now_jst():
            dt += timedelta(days=1)
        scheduled_at = dt.isoformat()

    item = {
        "id": draft.get("id") or f"{account}_{idx}",
        "account": account,
        "draft_idx": idx,
        "post_text": draft.get("post", ""),
        "image_prompt": draft.get("image_prompt", ""),
        "image_url": image_url or "",
        "scheduled_at": scheduled_at,
        "status": "scheduled",
        "approved_at": _now_jst().isoformat(),
    }
    queue = _load_queue()
    queue.append(item)
    _save_queue(queue)
    return item


def reject(account: str, idx: int) -> bool:
    """draft を却下（ファイル削除）"""
    date_str = _today_str()
    path = DRAFTS_DIR / account / date_str / f"{idx:02d}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def get_queue() -> list:
    """予約一覧（時刻順）"""
    queue = _load_queue()
    queue.sort(key=lambda x: x.get("scheduled_at", ""))
    return queue


def cancel_queued(item_id: str) -> bool:
    """予約をキャンセル"""
    queue = _load_queue()
    new_queue = [item for item in queue if item.get("id") != item_id]
    if len(new_queue) == len(queue):
        return False
    _save_queue(new_queue)
    return True


def pop_due(now: datetime | None = None) -> list:
    """投稿時刻が来たアイテムを返してキューから除外（cron が呼ぶ）"""
    now = now or _now_jst()
    queue = _load_queue()
    due = []
    remaining = []
    for item in queue:
        try:
            sched = datetime.fromisoformat(item["scheduled_at"])
        except Exception:
            remaining.append(item)
            continue
        if item.get("status") == "scheduled" and sched <= now:
            due.append(item)
        else:
            remaining.append(item)
    if due:
        _save_queue(remaining)
    return due


def mark_posted(item: dict, post_id: str, permalink: str) -> None:
    """投稿履歴に追加"""
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.append({
        **item,
        "status": "posted",
        "post_id": post_id,
        "permalink": permalink,
        "posted_at": _now_jst().isoformat(),
    })
    history = history[-200:]  # 直近200件
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

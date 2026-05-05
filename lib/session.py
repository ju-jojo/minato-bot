"""ユーザーセッション管理（chat_id 単位の投稿文・プロンプト保持）"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from . import config


JST = timezone(timedelta(hours=9))


def _session_path(chat_id: int) -> Path:
    return config.STATE_DIR / f"session_{chat_id}.json"


def _now() -> str:
    return datetime.now(JST).isoformat()


def load(chat_id: int) -> dict:
    path = _session_path(chat_id)
    if not path.exists():
        return {"post_text": None, "prompt": None, "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"post_text": None, "prompt": None, "updated_at": None}


def save(chat_id: int, data: dict) -> None:
    data["updated_at"] = _now()
    path = _session_path(chat_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_post_text(chat_id: int, text: str) -> None:
    s = load(chat_id)
    s["post_text"] = text
    save(chat_id, s)


def set_prompt(chat_id: int, prompt: str) -> None:
    s = load(chat_id)
    s["prompt"] = prompt
    save(chat_id, s)


def clear(chat_id: int) -> None:
    path = _session_path(chat_id)
    if path.exists():
        path.unlink()


# ---- 1メッセージ更新方式の処理セッション ----
def start_processing(chat_id: int, message_id: int, total: int, account: str = "minato") -> None:
    """朝の下書きレビュー開始時に呼ぶ"""
    s = load(chat_id)
    s["processing"] = {
        "message_id": message_id,
        "current_idx": 1,
        "total": total,
        "account": account,
        "image_waiting_idx": None,
        "results": {"approved": 0, "approved_with_image": 0, "rejected": 0, "skipped": 0},
        "started_at": _now(),
    }
    save(chat_id, s)


def get_processing(chat_id: int) -> dict | None:
    s = load(chat_id)
    return s.get("processing")


def update_processing(chat_id: int, **updates) -> dict | None:
    s = load(chat_id)
    p = s.get("processing")
    if not p:
        return None
    p.update(updates)
    s["processing"] = p
    save(chat_id, s)
    return p


def increment_result(chat_id: int, key: str) -> None:
    s = load(chat_id)
    p = s.get("processing")
    if not p:
        return
    p["results"][key] = p["results"].get(key, 0) + 1
    s["processing"] = p
    save(chat_id, s)


def end_processing(chat_id: int) -> None:
    s = load(chat_id)
    s.pop("processing", None)
    save(chat_id, s)


def status(chat_id: int) -> str:
    s = load(chat_id)
    parts = []
    if s.get("post_text"):
        preview = s["post_text"][:40] + ("..." if len(s["post_text"]) > 40 else "")
        parts.append(f"投稿文: {preview}")
    else:
        parts.append("投稿文: 未設定")
    if s.get("prompt"):
        preview = s["prompt"][:40] + ("..." if len(s["prompt"]) > 40 else "")
        parts.append(f"プロンプト: {preview}")
    else:
        parts.append("プロンプト: 未設定")
    if s.get("updated_at"):
        parts.append(f"更新: {s['updated_at']}")
    return "\n".join(parts)

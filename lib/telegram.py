"""Telegram Bot API クライアント（urllib のみ、外部依存なし）"""
import json
import ssl
import urllib.request
import urllib.parse
from . import config

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _request(method: str, params: dict | None = None) -> dict:
    url = f"{API_BASE}/{method}"
    data = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(chat_id: int, text: str, parse_mode: str | None = None,
                 disable_web_page_preview: bool = False) -> dict:
    params = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    return _request("sendMessage", params)


def get_file_path(file_id: str) -> str:
    """file_id からダウンロード可能な相対パスを取得"""
    result = _request("getFile", {"file_id": file_id})
    if not result.get("ok"):
        raise RuntimeError(f"getFile失敗: {result}")
    return result["result"]["file_path"]


def download_file(file_path: str) -> bytes:
    """Telegramサーバから画像バイト列を取得"""
    url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=30, context=_SSL_CTX) as resp:
        return resp.read()


def set_webhook(url: str, secret_token: str | None = None) -> dict:
    params = {
        "url": url,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    }
    if secret_token:
        params["secret_token"] = secret_token
    return _request("setWebhook", params)


def delete_webhook() -> dict:
    return _request("deleteWebhook", {"drop_pending_updates": True})


def get_webhook_info() -> dict:
    return _request("getWebhookInfo")

"""Threads Graph API クライアント（urllib のみ）"""
import json
import ssl
import time
import urllib.request
import urllib.parse
from . import config

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


API_BASE = "https://graph.threads.net/v1.0"


def _post(path: str, params: dict) -> dict:
    url = f"{API_BASE}/{path}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path: str, params: dict) -> dict:
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_image_container(image_url: str, text: str = "") -> str:
    """画像つき投稿のコンテナを作成し、creation_id を返す"""
    params = {
        "media_type": "IMAGE",
        "image_url": image_url,
        "access_token": config.THREADS_ACCESS_TOKEN,
    }
    if text:
        params["text"] = text
    result = _post(f"{config.THREADS_USER_ID}/threads", params)
    if "id" not in result:
        raise RuntimeError(f"create_image_container失敗: {result}")
    return result["id"]


def create_text_container(text: str) -> str:
    """テキストのみ投稿のコンテナを作成し、creation_id を返す"""
    params = {
        "media_type": "TEXT",
        "text": text,
        "access_token": config.THREADS_ACCESS_TOKEN,
    }
    result = _post(f"{config.THREADS_USER_ID}/threads", params)
    if "id" not in result:
        raise RuntimeError(f"create_text_container失敗: {result}")
    return result["id"]


def publish(creation_id: str) -> str:
    """コンテナを公開して、投稿IDを返す"""
    params = {
        "creation_id": creation_id,
        "access_token": config.THREADS_ACCESS_TOKEN,
    }
    result = _post(f"{config.THREADS_USER_ID}/threads_publish", params)
    if "id" not in result:
        raise RuntimeError(f"publish失敗: {result}")
    return result["id"]


def get_post_permalink(post_id: str) -> str:
    """投稿IDから permalink URL を取得"""
    params = {
        "fields": "permalink",
        "access_token": config.THREADS_ACCESS_TOKEN,
    }
    try:
        result = _get(post_id, params)
        return result.get("permalink", "")
    except Exception:
        return ""


def post_with_image(image_url: str, text: str) -> tuple[str, str]:
    """画像URL+テキストで投稿し、(post_id, permalink) を返す"""
    creation_id = create_image_container(image_url, text)
    # コンテナ準備に少し時間がかかるため待機
    time.sleep(3)
    post_id = publish(creation_id)
    permalink = get_post_permalink(post_id)
    return post_id, permalink


def post_text_only(text: str) -> tuple[str, str]:
    """テキストのみで投稿し、(post_id, permalink) を返す"""
    creation_id = create_text_container(text)
    time.sleep(2)
    post_id = publish(creation_id)
    permalink = get_post_permalink(post_id)
    return post_id, permalink

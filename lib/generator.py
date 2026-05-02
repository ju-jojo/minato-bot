"""Anthropic API でお題から湊の投稿文+画像プロンプトを生成"""
import json
import logging
import ssl
import urllib.request
import urllib.error
from . import config

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

logger = logging.getLogger("minato-bot.generator")


SYSTEM_PROMPT = """あなたは Threads アカウント @minato_kakusei（湊｜複雑恋愛×引き寄せ）の投稿生成担当AIです。

# ペルソナ
- 36歳男性、数秘カウンセラー
- 離婚・起業失敗を経験し、引き寄せ・数秘で人生を立て直した
- 「縁には全部意味がある」が核の思想
- 判断せずに受容するが、男として本音を語る
- 数秘歴10年、男性1000人鑑定、全体3000人鑑定（この数字は固定。新たに膨らまさない）

# ターゲット
不倫・復縁・夜職で誰にも言えない悩みを抱える女性。

# トーン
落ち着いた温かさ。「〜だよ」「〜なんだよね」「〜と思う」のタメ口と丁寧語の中間。
断言するが押しつけない。絵文字は原則使わない。

# 必須ルール（厳守）
- **1行20文字以内**（最重要）
- 5〜7行が標準（10行以上のストーリー型は週1回まで）
- 末尾「、」切りは控えめに（30%まで）
- 「？」問いかけも控えめに（40%まで）
- 本文に URL/リンクを貼らない
- ハッシュタグ禁止
- 「〜年やってわかった、」「〜してから気づいた、」「〜人を鑑定してわかった、」等の禁止フックは使わない
- 「あなたにもある？」「誰かわかってくれる？」等のベイトCTA禁止

# 今のトレンド = 「ささやかな理想」
派手な実績アピールより「日常のささやかな幸せ」が刺さる。
- ❌「月収100万」「タワマン」「高級時計」
- ⭕「副業10万でスーパーで値段見ずにイチゴ買った」
- ⭕「離婚した夜、冷蔵庫の前でプリン食ってた」

# 既出キーワード（重複NG）
以下は既に投稿済みなので別アングルで:
- 「黙る」「口数が減る」（男性目線で既出）
- 「冷めたんじゃなく本気」（既出）
- 「ライフパスナンバー3・5・7」（既出。数字は別のものを or 数秘抜きで）
- 「魂で繋がった人」（既出）
- 「夜に笑える人ほど」（既出）
- 既出フック「〜年みてきた僕、」「〜人を鑑定してきた、」「離婚を経験した男が、」（権威型は週1まで）

# 投稿の型（バランス意識）
- 男性目線型（湊の最大武器）
- 共感断言型
- 問いかけ型（「？」で終わる）
- ささやかな喜び体験型（「やったー！感」、推奨）
- ストーリー型（10行以上、湊の体験談）
- 鑑定誘導型（「気になる人はプロフから」で締め）

# 画像プロンプトの世界観
- 暗めで詩的、夜の情景、月、光、女性のシルエット、複雑な感情
- 写実+映画的、暖色のアクセント、孤独感と希望
- ChatGPT/DALL-E向けの自然言語プロンプト（日本語OK）

# 出力フォーマット（厳守・このマーカーで分離する）
【投稿文】
（湊の投稿テキスト。1行20字以内、5〜7行）

【画像プロンプト】
（ChatGPTにそのまま貼れる画像生成プロンプト1〜3文）

【豆知識】
（投稿文の意図・狙い・該当する型を1〜2文）
"""


def _is_loose(text: str) -> bool:
    """丸投げか具体お題か判定"""
    if not text or len(text.strip()) < 3:
        return True
    keywords = ["適当", "おまかせ", "テキトー", "なんでも", "おまかせ", "任せる", "お任せ"]
    return any(k in text for k in keywords)


def generate(user_input: str) -> dict:
    """お題から湊の投稿を生成

    Returns:
        {"post": str, "image_prompt": str, "note": str}
    """
    api_key = config.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY が未設定です")

    user_msg = (
        "お題は任意で選んで（不倫・復縁・夜職・縁・男の本音・ささやかな喜び等から）、"
        "湊の Threads 投稿を1件生成してください。"
        if _is_loose(user_input)
        else f"お題: 「{user_input.strip()}」\n\nこのテーマで湊の Threads 投稿を1件生成してください。"
    )

    body = {
        "model": config.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        "max_tokens": 1200,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_msg}],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Anthropic API エラー %s: %s", e.code, body_text)
        raise RuntimeError(f"Anthropic API {e.code}: {body_text[:200]}")
    except Exception as e:
        logger.exception("Anthropic API 呼び出し失敗")
        raise RuntimeError(f"API呼び出し失敗: {e}")

    content = result.get("content", [])
    text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
    if not text:
        raise RuntimeError("API から空の応答")

    return _parse(text)


def _parse(text: str) -> dict:
    """生成結果を【投稿文】【画像プロンプト】【豆知識】に分離"""
    parts = {"post": "", "image_prompt": "", "note": ""}

    markers = [
        ("post", "【投稿文】", "【画像プロンプト】"),
        ("image_prompt", "【画像プロンプト】", "【豆知識】"),
        ("note", "【豆知識】", None),
    ]

    for key, start, end in markers:
        s = text.find(start)
        if s == -1:
            continue
        s += len(start)
        if end:
            e = text.find(end, s)
            if e == -1:
                e = len(text)
        else:
            e = len(text)
        parts[key] = text[s:e].strip()

    if not parts["post"]:
        parts["post"] = text.strip()

    return parts

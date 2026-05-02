"""Telegram メッセージのコマンドハンドラ"""
import logging
import urllib.parse
import uuid
from pathlib import Path
from . import config, generator, session, telegram, threads


logger = logging.getLogger("minato-bot.handler")


HELP_TEXT = """🤖 湊Threads投稿Bot

【AI生成（NEW）】
✨ お題を送るだけ → 投稿文+画像プロンプトを生成
   例:「不倫の本音」「夜中のLINE」
✨「適当に」「おまかせ」→ AIにテーマ任せ
✨ /gen <お題> → 明示的に生成

【投稿フロー】
1️⃣ /post <投稿文> → 投稿文を保存
2️⃣ /prompt <プロンプト> → プロンプト保存＋ChatGPT起動リンク返信
3️⃣ ChatGPTで生成した画像をこのトークに送信 → Threads自動投稿

【その他】
/status → 今のセッション確認
/reset → セッションをリセット
/help → このヘルプ

⚠️ /post を使わずに画像だけ送ると「投稿文なしで投稿」となります。"""


def is_authorized(chat_id: int) -> bool:
    """許可されたユーザーか確認"""
    if not config.TELEGRAM_CHAT_ID:
        return True  # 制限なし設定（開発時）
    return str(chat_id) == str(config.TELEGRAM_CHAT_ID)


def handle_command(chat_id: int, text: str) -> None:
    """テキストメッセージ（コマンド含む）を処理"""
    text = text.strip()

    if text in ("/start", "/help"):
        telegram.send_message(chat_id, HELP_TEXT)
        return

    if text in ("/reset", "/cancel"):
        session.clear(chat_id)
        telegram.send_message(chat_id, "✅ セッションをリセットしました")
        return

    if text == "/status":
        telegram.send_message(chat_id, session.status(chat_id))
        return

    if text.startswith("/post"):
        body = text[len("/post"):].strip()
        if not body:
            telegram.send_message(chat_id, "❌ 使い方: `/post 投稿文`")
            return
        session.set_post_text(chat_id, body)
        preview = body[:60] + ("..." if len(body) > 60 else "")
        telegram.send_message(
            chat_id,
            f"✅ 投稿文を保存しました\n\n{preview}\n\n次は /prompt でプロンプトを送るか、画像を送って投稿。"
        )
        return

    if text.startswith("/prompt"):
        body = text[len("/prompt"):].strip()
        if not body:
            telegram.send_message(chat_id, "❌ 使い方: `/prompt 画像生成プロンプト`")
            return
        session.set_prompt(chat_id, body)
        encoded = urllib.parse.quote(body)
        chatgpt_url = f"https://chatgpt.com/?q={encoded}"
        msg = (
            "✅ プロンプトを保存しました\n\n"
            f"<a href=\"{chatgpt_url}\">▶ ChatGPTで画像生成</a>\n\n"
            "生成した画像をこのトークに送信すれば、Threadsへ投稿します。"
        )
        telegram.send_message(chat_id, msg, parse_mode="HTML",
                              disable_web_page_preview=True)
        return

    if text.startswith("/gen"):
        body = text[len("/gen"):].strip()
        _generate_and_send(chat_id, body or "適当に")
        return

    # コマンド以外のテキスト → AI生成のお題として扱う
    if text and not text.startswith("/"):
        _generate_and_send(chat_id, text)
        return

    # 不明なスラッシュコマンド
    telegram.send_message(
        chat_id,
        "❓ 不明なコマンドです。/help で使い方を表示。"
    )


def _generate_and_send(chat_id: int, prompt: str) -> None:
    """お題から AI 生成して Telegram に分割送信"""
    telegram.send_message(chat_id, "✍️ 生成中...（10〜20秒）")

    try:
        result = generator.generate(prompt)
    except Exception as e:
        logger.exception("生成失敗")
        telegram.send_message(chat_id, f"❌ 生成失敗: {e}")
        return

    post_text = result.get("post", "").strip()
    image_prompt = result.get("image_prompt", "").strip()
    note = result.get("note", "").strip()

    # メッセージ1: 投稿文のみ（ラベルなし・タップしてコピー可能）
    if post_text:
        telegram.send_message(chat_id, post_text)
        session.set_post_text(chat_id, post_text)

    # メッセージ2: 画像プロンプトのみ（ラベルなし・タップしてコピー可能）
    if image_prompt:
        telegram.send_message(chat_id, image_prompt)

    # メッセージ3: 豆知識 + ChatGPTリンク + 操作案内（補足情報を1メッセージに集約）
    extra_lines = []
    if note:
        extra_lines.append(f"💡 {note}")
    if image_prompt:
        encoded = urllib.parse.quote(image_prompt)
        chatgpt_url = f"https://chatgpt.com/?q={encoded}"
        extra_lines.append(f'<a href="{chatgpt_url}">▶ ChatGPTで画像生成</a>')
        extra_lines.append("画像をこのトークに送ると Threads 投稿します")
    if extra_lines:
        telegram.send_message(
            chat_id,
            "\n\n".join(extra_lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def handle_photo(chat_id: int, photo_array: list, caption: str | None = None) -> None:
    """画像受信時の処理: Threadsへ投稿"""
    sess = session.load(chat_id)
    post_text = sess.get("post_text") or caption or ""

    if not post_text:
        telegram.send_message(
            chat_id,
            "⚠️ 投稿文がありません。先に /post 投稿文 を送ってください。"
        )
        return

    # 最大解像度を選択
    largest = max(photo_array, key=lambda p: p.get("file_size", 0))
    file_id = largest["file_id"]

    telegram.send_message(chat_id, "📥 画像を受信。Threadsへ投稿中…")

    try:
        file_path = telegram.get_file_path(file_id)
        image_bytes = telegram.download_file(file_path)
    except Exception as e:
        logger.exception("画像DL失敗")
        telegram.send_message(chat_id, f"❌ 画像取得失敗: {e}")
        return

    # ローカル保存（公開URL用）
    ext = Path(file_path).suffix or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = config.IMAGES_DIR / filename
    save_path.write_bytes(image_bytes)
    image_url = f"{config.PUBLIC_BASE_URL.rstrip('/')}/images/{filename}"

    try:
        post_id, permalink = threads.post_with_image(image_url, post_text)
    except Exception as e:
        logger.exception("Threads投稿失敗")
        telegram.send_message(chat_id, f"❌ Threads投稿失敗: {e}")
        return

    # セッションクリア
    session.clear(chat_id)

    msg = f"✅ 投稿完了\n\n投稿ID: {post_id}"
    if permalink:
        msg += f"\n🔗 {permalink}"
    telegram.send_message(chat_id, msg, disable_web_page_preview=False)


def handle_update(update: dict) -> None:
    """Telegram update を受け取って分岐"""
    message = update.get("message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return

    if not is_authorized(chat_id):
        logger.warning(f"未承認のchat_id: {chat_id}")
        telegram.send_message(chat_id, "❌ このBotは承認されたユーザー専用です")
        return

    if "photo" in message:
        handle_photo(chat_id, message["photo"], message.get("caption"))
        return

    text = message.get("text")
    if text:
        handle_command(chat_id, text)
        return

    telegram.send_message(
        chat_id,
        "❓ テキストか画像を送ってください。/help で使い方。"
    )

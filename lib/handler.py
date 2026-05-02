"""Telegram メッセージのコマンドハンドラ"""
import logging
import urllib.parse
import uuid
from pathlib import Path
from . import config, drafts, generator, session, telegram, threads


logger = logging.getLogger("minato-bot.handler")


HELP_TEXT = """🤖 湊Threads投稿Bot

【夜間自動下書き → 朝承認フロー（NEW）】
毎晩2時にローカルで下書き10本生成 → /list で確認 → /approve で予約投稿
/list → 今日の下書き一覧
/show N → N番の全文表示
/approve N → N番を次の空きスロットで予約
/approve N HH:MM → 時刻指定で予約
/reject N → 却下
/queue → 予約一覧
/cancel <id> → 予約キャンセル

【AI生成（その場で1本）】
お題を送るだけ → 投稿文+画像プロンプトを生成
例:「不倫の本音」「夜中のLINE」
「適当に」「おまかせ」→ AIにテーマ任せ
/gen <お題> → 明示的に生成

【手動投稿フロー】
1️⃣ /post <投稿文> → 投稿文を保存
2️⃣ /prompt <プロンプト> → プロンプト保存＋ChatGPT起動リンク返信
3️⃣ ChatGPTで生成した画像をこのトークに送信 → Threads自動投稿

【その他】
/status → 今のセッション確認
/reset → セッションをリセット
/help → このヘルプ"""


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

    # ---- threads-company 連携: 下書き承認系 ----
    if text == "/list":
        _handle_list(chat_id)
        return

    if text.startswith("/show"):
        _handle_show(chat_id, text[len("/show"):].strip())
        return

    if text.startswith("/approve"):
        _handle_approve(chat_id, text[len("/approve"):].strip())
        return

    if text.startswith("/reject"):
        _handle_reject(chat_id, text[len("/reject"):].strip())
        return

    if text == "/queue":
        _handle_queue(chat_id)
        return

    if text.startswith("/cancel "):
        _handle_cancel(chat_id, text[len("/cancel"):].strip())
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


# ---- 下書き承認系ハンドラ ----
def _handle_list(chat_id: int) -> None:
    items = drafts.list_drafts("minato")
    if not items:
        telegram.send_message(chat_id, "今日の下書きはまだ生成されていません。\n夜2時の自動生成を待つか /gen で個別生成してください。")
        return
    lines = [f"📋 今日の下書き ({len(items)}本)", ""]
    for d in items:
        head = d.get("post", "").split("\n")[0][:30]
        lines.append(f"[{d['idx']:02d}] {head}")
    lines.append("")
    lines.append("/show N で全文 / /approve N で承認 / /reject N で却下")
    telegram.send_message(chat_id, "\n".join(lines))


def _handle_show(chat_id: int, arg: str) -> None:
    if not arg.isdigit():
        telegram.send_message(chat_id, "❌ 使い方: /show 1")
        return
    idx = int(arg)
    d = drafts.get_draft("minato", idx)
    if not d:
        telegram.send_message(chat_id, f"❌ #{idx} は存在しません")
        return
    # 投稿文だけ単独でコピペ可能に
    if d.get("post"):
        telegram.send_message(chat_id, d["post"])
    if d.get("image_prompt"):
        telegram.send_message(chat_id, d["image_prompt"])
    extra = []
    if d.get("note"):
        extra.append(f"💡 {d['note']}")
    extra.append(f"承認: /approve {idx}  (時刻指定: /approve {idx} 12:00)")
    extra.append(f"却下: /reject {idx}")
    telegram.send_message(chat_id, "\n".join(extra))


def _handle_approve(chat_id: int, arg: str) -> None:
    parts = arg.split()
    if not parts or not parts[0].isdigit():
        telegram.send_message(chat_id, "❌ 使い方: /approve 1  または  /approve 1 12:00")
        return
    idx = int(parts[0])
    scheduled_at = parts[1] if len(parts) >= 2 else None
    try:
        item = drafts.approve("minato", idx, scheduled_at)
    except Exception as e:
        telegram.send_message(chat_id, f"❌ 承認失敗: {e}")
        return
    sched = item["scheduled_at"][:16].replace("T", " ")
    telegram.send_message(
        chat_id,
        f"✅ #{idx} を承認・予約しました\n投稿時刻: {sched} (JST)\n\n/queue で予約一覧"
    )


def _handle_reject(chat_id: int, arg: str) -> None:
    if not arg.isdigit():
        telegram.send_message(chat_id, "❌ 使い方: /reject 1")
        return
    idx = int(arg)
    if drafts.reject("minato", idx):
        telegram.send_message(chat_id, f"✅ #{idx} を却下しました")
    else:
        telegram.send_message(chat_id, f"❌ #{idx} は存在しません")


def _handle_queue(chat_id: int) -> None:
    queue = drafts.get_queue()
    if not queue:
        telegram.send_message(chat_id, "📭 予約はありません")
        return
    lines = [f"📅 予約一覧 ({len(queue)}本)", ""]
    for item in queue:
        sched = item["scheduled_at"][:16].replace("T", " ")
        head = item.get("post_text", "").split("\n")[0][:25]
        lines.append(f"{sched}  [{item['draft_idx']:02d}] {head}")
    lines.append("")
    lines.append("キャンセル: /cancel <id> ※ID は本文中の番号ではなくキューid")
    telegram.send_message(chat_id, "\n".join(lines))


def _handle_cancel(chat_id: int, arg: str) -> None:
    if not arg:
        telegram.send_message(chat_id, "❌ 使い方: /cancel <id>")
        return
    if drafts.cancel_queued(arg):
        telegram.send_message(chat_id, f"✅ {arg} をキャンセルしました")
    else:
        telegram.send_message(chat_id, f"❌ {arg} は予約一覧にありません")


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

"""Telegram メッセージのコマンドハンドラ"""
import logging
import re
import urllib.parse
import uuid
from pathlib import Path
from . import config, drafts, feedback, generator, session, telegram, threads


def _parse_int_arg(arg: str) -> int | None:
    """全角・記号混じりからも整数を抽出 ('1' '#1' '１' '　1' 全部対応)"""
    if not arg:
        return None
    # 全角数字を半角に
    arg = arg.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    m = re.search(r"\d+", arg)
    return int(m.group()) if m else None


logger = logging.getLogger("minato-bot.handler")


HELP_TEXT = """🤖 湊Threads投稿Bot

【夜間自動下書き → 朝承認フロー（NEW）】
毎晩2時にローカルで下書き10本生成 → /list で確認 → /approve で予約投稿
/list → 今日の下書き一覧
/show N → N番の全文表示
/approve N → N番を次の空きスロットで予約
/approve N HH:MM → 時刻指定で予約
/reject N → 却下（理由付きで AI 学習: /reject N 冒頭が弱い 等）
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
    idx = _parse_int_arg(arg)
    logger.info(f"/show 受信: arg={arg!r} parsed_idx={idx}")
    if idx is None:
        telegram.send_message(chat_id, f"❌ 番号が読み取れませんでした: 「{arg}」\n半角数字で /show 1 のように送ってください")
        return
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
    idx = _parse_int_arg(parts[0]) if parts else None
    logger.info(f"/approve 受信: arg={arg!r} parsed_idx={idx}")
    if idx is None:
        telegram.send_message(chat_id, "❌ 使い方: /approve 1  または  /approve 1 12:00")
        return
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
    parts = arg.split(None, 1)
    idx = _parse_int_arg(parts[0]) if parts else None
    reason = parts[1].strip() if len(parts) > 1 else ""
    logger.info(f"/reject 受信: arg={arg!r} parsed_idx={idx} reason={reason!r}")
    if idx is None:
        telegram.send_message(chat_id, "❌ 使い方: /reject 1\n  または /reject 1 冒頭が弱い、固有名詞がない")
        return

    # 却下前に投稿本文を取得（feedback 用）
    draft = drafts.get_draft("minato", idx)
    draft_post = draft.get("post", "") if draft else None

    if not drafts.reject("minato", idx):
        telegram.send_message(chat_id, f"❌ #{idx} は存在しません")
        return

    # 理由があれば feedback.md に追記
    if reason:
        try:
            feedback.append_feedback("minato", reason, draft_post)
            telegram.send_message(
                chat_id,
                f"✅ #{idx} を却下＋フィードバック記録しました\n\n"
                f"📝 改善指示: {reason}\n\n"
                f"次回生成時に AI が反映します。"
            )
        except Exception as e:
            logger.exception("feedback追記失敗")
            telegram.send_message(chat_id, f"✅ #{idx} を却下しました（feedback記録失敗: {e}）")
    else:
        telegram.send_message(
            chat_id,
            f"✅ #{idx} を却下しました\n\n"
            f"💡 ヒント: /reject {idx} 理由 と書くと AI が次回から学習します"
        )


def _handle_queue(chat_id: int) -> None:
    queue = drafts.get_queue()
    if not queue:
        telegram.send_message(chat_id, "📭 予約はありません")
        return
    buttons = []
    for item in queue:
        sched = item["scheduled_at"][:16].replace("T", " ")
        head = item.get("post_text", "").split("\n")[0][:20]
        label = f"{sched}  [{item['draft_idx']:02d}] {head}"
        buttons.append([(label, f"act:cancel:{item['id']}")])
    keyboard = telegram.build_inline_keyboard(buttons)
    telegram.send_message(chat_id, f"📅 予約一覧 ({len(queue)}本)\nタップでキャンセル", reply_markup=keyboard)


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
    """画像受信時の処理: 1メッセージ更新方式 か 旧手動フロー"""
    largest = max(photo_array, key=lambda p: p.get("file_size", 0))
    file_id = largest["file_id"]

    # ===== 1メッセージ更新方式: 画像待ちモード =====
    p = session.get_processing(chat_id)
    if p and p.get("image_waiting_idx"):
        idx = p["image_waiting_idx"]
        message_id = p["message_id"]
        try:
            file_path = telegram.get_file_path(file_id)
            image_bytes = telegram.download_file(file_path)
            ext = Path(file_path).suffix or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            save_path = config.IMAGES_DIR / filename
            save_path.write_bytes(image_bytes)
            image_url = f"{config.PUBLIC_BASE_URL.rstrip('/')}/images/{filename}"
            item = drafts.approve("minato", idx, image_url=image_url)
        except Exception as e:
            logger.exception("画像つき承認失敗")
            telegram.send_message(chat_id, f"❌ 画像処理失敗: {e}")
            return
        sched = item["scheduled_at"][5:16].replace("T", " ")
        telegram.send_message(chat_id, f"✅ #{idx} 画像つき投稿予約: {sched}")
        session.increment_result(chat_id, "approved_with_image")
        _advance_or_finish(chat_id, message_id)
        return

    # ===== 旧手動フロー =====
    sess = session.load(chat_id)
    post_text = sess.get("post_text") or caption or ""
    if not post_text:
        telegram.send_message(
            chat_id,
            "⚠️ 投稿文がありません。先に /post 投稿文 を送ってください。"
        )
        return

    telegram.send_message(chat_id, "📥 画像を受信。Threadsへ投稿中…")
    try:
        file_path = telegram.get_file_path(file_id)
        image_bytes = telegram.download_file(file_path)
    except Exception as e:
        logger.exception("画像DL失敗")
        telegram.send_message(chat_id, f"❌ 画像取得失敗: {e}")
        return

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

    session.clear(chat_id)
    msg = f"✅ 投稿完了\n\n投稿ID: {post_id}"
    if permalink:
        msg += f"\n🔗 {permalink}"
    telegram.send_message(chat_id, msg, disable_web_page_preview=False)


def handle_update(update: dict) -> None:
    """Telegram update を受け取って分岐"""
    # 1) callback_query (ボタンタップ)
    cq = update.get("callback_query")
    if cq:
        handle_callback_query(cq)
        return

    # 2) message
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
        p = session.get_processing(chat_id)
        # 訂正テキストの受信
        if p and p.get("editing_idx"):
            idx = p["editing_idx"]
            msg_id = p["editing_message_id"]
            session.update_processing(chat_id, editing_idx=None, editing_message_id=None)
            if drafts.update_draft_post("minato", idx, text):
                updated = drafts.get_draft("minato", idx)
                display_text = _format_draft_message(idx, p["total"], updated)
                buttons = _build_draft_buttons(idx, updated.get("image_recommended", False))
                telegram.edit_message_text(chat_id, msg_id, display_text, reply_markup=buttons)
                telegram.send_message(chat_id, f"✅ #{idx} を訂正しました")
            else:
                telegram.send_message(chat_id, f"❌ #{idx} の訂正に失敗しました")
            return
        # 却下後の理由リプライを feedback として記録
        if message.get("reply_to_message"):
            if p and p.get("last_rejected_idx"):
                try:
                    feedback.append_feedback("minato", text, p.get("last_rejected_post"))
                    session.update_processing(chat_id, last_rejected_idx=None, last_rejected_post=None)
                    telegram.send_message(chat_id, f"📝 フィードバック記録\n\n「{text[:60]}」\n\n次回生成時に AI が反映します")
                    return
                except Exception:
                    logger.exception("feedback記録失敗")
        handle_command(chat_id, text)
        return

    telegram.send_message(
        chat_id,
        "❓ テキストか画像を送ってください。/help で使い方。"
    )


# ===== 1メッセージ更新方式 =====

def _format_draft_message(idx: int, total: int, draft: dict) -> str:
    post = draft.get("post", "").strip()
    note = draft.get("note", "").strip()
    image_rec = draft.get("image_recommended", False)
    parts = [
        f"📋 {idx} / {total}",
        "─" * 16,
        post,
        "─" * 16,
    ]
    rec_label = "🎨 画像つき推奨" if image_rec else "✏️ テキストのみ推奨"
    parts.append(rec_label)
    if note:
        parts.append(f"💡 {note[:80]}")
    return "\n".join(parts)


def _build_draft_buttons(idx: int, image_recommended: bool = False) -> dict:
    """推奨ボタンを左に置く（タップしやすく）"""
    if image_recommended:
        return telegram.build_inline_keyboard([
            [("🎨 画像つき", f"act:image:{idx}"), ("✅ テキストのみ", f"act:approve:{idx}")],
            [("✏️ 訂正", f"act:edit:{idx}"), ("❌ 却下", f"act:reject:{idx}"), ("⏭ スキップ", f"act:skip:{idx}")],
        ])
    return telegram.build_inline_keyboard([
        [("✅ 投稿", f"act:approve:{idx}"), ("🎨 画像つき", f"act:image:{idx}")],
        [("✏️ 訂正", f"act:edit:{idx}"), ("❌ 却下", f"act:reject:{idx}"), ("⏭ スキップ", f"act:skip:{idx}")],
    ])


def start_review(chat_id: int, account: str = "minato") -> dict:
    """upload_drafts 完了時に呼ぶ。最初のメッセージを送信して processing 開始"""
    items = drafts.list_drafts(account)
    if not items:
        return telegram.send_message(chat_id, "下書きがありません")

    total = len(items)
    first = items[0]
    text = _format_draft_message(1, total, first)
    buttons = _build_draft_buttons(first["idx"], first.get("image_recommended", False))
    result = telegram.send_message(chat_id, text, reply_markup=buttons)
    msg_id = result.get("result", {}).get("message_id")
    if msg_id:
        session.start_processing(chat_id, msg_id, total, account)
    return result


def _advance_or_finish(chat_id: int, message_id: int) -> None:
    p = session.get_processing(chat_id)
    if not p:
        return
    account = p["account"]
    items = drafts.list_drafts(account)
    # current_idx の次の draft を探す（rejected/skipped でファイル削除されてる場合考慮）
    current = p["current_idx"]
    next_item = None
    for it in items:
        if it["idx"] > current:
            next_item = it
            break

    if not next_item:
        # 終了サマリー
        r = p["results"]
        total = p["total"]
        summary_parts = [f"✅ {total}本のレビュー完了", ""]
        if r.get("approved"):
            summary_parts.append(f"✅ テキストで投稿予約: {r['approved']}本")
        if r.get("approved_with_image"):
            summary_parts.append(f"🎨 画像つき投稿予約: {r['approved_with_image']}本")
        if r.get("rejected"):
            summary_parts.append(f"❌ 却下: {r['rejected']}本")
        if r.get("skipped"):
            summary_parts.append(f"⏭ スキップ: {r['skipped']}本")
        summary_parts.append("")
        summary_parts.append("/queue で予約一覧を確認できます")
        telegram.edit_message_text(chat_id, message_id, "\n".join(summary_parts),
                                   reply_markup={"inline_keyboard": []})
        session.end_processing(chat_id)
        return

    # 次の下書きへ
    session.update_processing(chat_id, current_idx=next_item["idx"], image_waiting_idx=None)
    text = _format_draft_message(next_item["idx"], p["total"], next_item)
    buttons = _build_draft_buttons(next_item["idx"], next_item.get("image_recommended", False))
    telegram.edit_message_text(chat_id, message_id, text, reply_markup=buttons)


def handle_callback_query(cq: dict) -> None:
    cq_id = cq.get("id", "")
    chat_id = cq.get("message", {}).get("chat", {}).get("id")
    message_id = cq.get("message", {}).get("message_id")
    data = cq.get("data", "")
    logger.info(f"callback_query: chat_id={chat_id} data={data!r}")

    if not is_authorized(chat_id):
        telegram.answer_callback_query(cq_id, "❌ 未承認のユーザー", show_alert=True)
        return

    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "act":
        telegram.answer_callback_query(cq_id, "❌ 不明な操作")
        return

    action = parts[1]
    try:
        idx = int(parts[2])
    except ValueError:
        telegram.answer_callback_query(cq_id, "❌ 不正なidx")
        return

    if action == "approve":
        _cb_approve(chat_id, message_id, idx, cq_id)
    elif action == "image":
        _cb_image_request(chat_id, message_id, idx, cq_id)
    elif action == "reject":
        _cb_reject(chat_id, message_id, idx, cq_id)
    elif action == "skip":
        _cb_skip(chat_id, message_id, idx, cq_id)
    elif action == "edit":
        _cb_edit(chat_id, message_id, idx, cq_id)
    elif action == "cancel":
        item_id = ":".join(parts[2:])
        _cb_cancel_queue(chat_id, message_id, item_id, cq_id)
    else:
        telegram.answer_callback_query(cq_id, f"❌ 不明: {action}")


def _cb_approve(chat_id: int, message_id: int, idx: int, cq_id: str) -> None:
    try:
        item = drafts.approve("minato", idx)
    except Exception as e:
        logger.exception("approve失敗")
        telegram.answer_callback_query(cq_id, f"❌ {e}", show_alert=True)
        return
    sched = item["scheduled_at"][5:16].replace("T", " ")
    telegram.answer_callback_query(cq_id, f"✅ {sched} に予約しました")
    session.increment_result(chat_id, "approved")
    _advance_or_finish(chat_id, message_id)


def _cb_image_request(chat_id: int, message_id: int, idx: int, cq_id: str) -> None:
    draft = drafts.get_draft("minato", idx)
    if not draft:
        telegram.answer_callback_query(cq_id, "❌ 下書きなし", show_alert=True)
        return
    image_prompt = draft.get("image_prompt", "")
    if not image_prompt:
        telegram.answer_callback_query(cq_id, "❌ 画像プロンプトなし", show_alert=True)
        return

    telegram.answer_callback_query(cq_id, "🎨 画像生成して送ってください")
    # ラベルなしで本文だけ（コピペしやすく）
    telegram.send_message(chat_id, image_prompt)
    encoded = urllib.parse.quote(image_prompt)
    chatgpt_url = f"https://chatgpt.com/?q={encoded}"
    telegram.send_message(
        chat_id,
        f'<a href="{chatgpt_url}">▶ ChatGPTで画像生成</a>\n\n生成した画像をこのトークに送信 → 自動投稿予約',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    p = session.get_processing(chat_id)
    total = p["total"] if p else "?"
    text = (
        f"📋 {idx} / {total}\n"
        f"🎨 画像待ち中...\n\n"
        f"{draft.get('post', '')}\n\n"
        f"画像送信後、自動で次の下書きへ進みます。"
    )
    telegram.edit_message_text(chat_id, message_id, text, reply_markup={"inline_keyboard": []})
    session.update_processing(chat_id, image_waiting_idx=idx)


def _cb_reject(chat_id: int, message_id: int, idx: int, cq_id: str) -> None:
    draft = drafts.get_draft("minato", idx)
    draft_post = draft.get("post", "") if draft else None
    drafts.reject("minato", idx)
    telegram.answer_callback_query(cq_id, "❌ 却下しました")
    session.increment_result(chat_id, "rejected")
    # 理由入力を促す（force_reply）
    telegram.send_message(
        chat_id,
        f"#{idx} 却下。理由があれば**このメッセージに返信**してください（学習に反映）",
        reply_markup={"force_reply": True, "input_field_placeholder": "例: 冒頭が弱い、固有名詞がない"},
    )
    # rejected_idx を session に記録（リプライ受信時に紐づけ用）
    session.update_processing(chat_id, last_rejected_idx=idx, last_rejected_post=draft_post)
    _advance_or_finish(chat_id, message_id)


def _cb_skip(chat_id: int, message_id: int, idx: int, cq_id: str) -> None:
    telegram.answer_callback_query(cq_id, "⏭ スキップ")
    session.increment_result(chat_id, "skipped")
    _advance_or_finish(chat_id, message_id)


def _cb_cancel_queue(chat_id: int, message_id: int, item_id: str, cq_id: str) -> None:
    if drafts.cancel_queued(item_id):
        telegram.answer_callback_query(cq_id, "✅ キャンセルしました")
        # ボタンを削除して完了表示に更新
        try:
            telegram.edit_message_text(
                chat_id, message_id,
                "✅ キャンセル済み",
                reply_markup={"inline_keyboard": []},
            )
        except Exception:
            pass
        # 残りのキューを再表示
        _handle_queue(chat_id)
    else:
        telegram.answer_callback_query(cq_id, "❌ キャンセル失敗（既に投稿済みかも）", show_alert=True)


def _cb_edit(chat_id: int, message_id: int, idx: int, cq_id: str) -> None:
    telegram.answer_callback_query(cq_id, "✏️ 訂正モード")
    session.update_processing(chat_id, editing_idx=idx, editing_message_id=message_id)
    telegram.send_message(
        chat_id,
        f"✏️ #{idx} の訂正文を送信してください\n\n訂正後、ボタンが再表示されます。",
        reply_markup={"force_reply": True, "input_field_placeholder": "訂正した投稿文をここに入力"},
    )

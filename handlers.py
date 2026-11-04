from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Poll,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import BadRequest

import database as db
from config import ADMIN_IDS, SUBJECTS_DIR, TIMEOUT_PER_QUESTION, WEBAPP_URL
from parser import list_subjects, load_subject

log = logging.getLogger(__name__)


# ---------- helpers ----------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📝 🎯 ابدأ الاختبار", callback_data="menu:srt")],
    ]
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("🌐 🌐 الاختبار النهائي", web_app=WebAppInfo(url=WEBAPP_URL))])
    rows.extend([
        [InlineKeyboardButton("📊 🏆 نتائجي", callback_data="menu:my")],
        [InlineKeyboardButton("🏆 🥇 أفضل النتائج", callback_data="menu:top")],
        [InlineKeyboardButton("⚙️ ⚙️ الإعدادات", callback_data="menu:settings")],
        [InlineKeyboardButton("ℹ️ ℹ️ حول البوت", callback_data="menu:about")],
    ])
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("👑 👨‍💻 لوحة الإدارة", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ 🏠 القائمة الرئيسية", callback_data="menu:home")]]
    )


def escape_md(text: str) -> str:
    # We use plain text everywhere — but if a question has special chars,
    # they are safe because we do NOT use ParseMode by default.
    return text


# ---------- helpers for chat type ----------

def _is_group(chat) -> bool:
    return chat and chat.type in ("group", "supergroup")


async def _show_subject_picker(message, is_group: bool) -> None:
    subjects = list_subjects()
    if not subjects:
        await message.reply_text("⚠️ لا توجد مواضيع متاحة حالياً.")
        return
    rows = []
    for name, path in subjects:
        rows.append([InlineKeyboardButton(f"📘 {name}", callback_data=f"subj:{path.stem}")])
    text = (
        "📚 *📚 اختر موضوعاً لبدء الاختبار الجماعي:*"
        if is_group
        else "📚 📚 اختر موضوعاً:"
    )
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)


# ---------- /srt ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    db.upsert_user(user.id, user.username or "", user.first_name or "", user.last_name or "")

    if _is_group(chat):
        text = (
            f"👋 *بوت الاختبارات في المجموعة!*\n\n"
            f"هنا يمكنك إجراء اختبار جماعي — جميع الأعضاء يستطيعون المشاركة.\n\n"
            f"لبدء الاختبار أرسل الأمر /quiz."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    text = (
        f"السلام عليكم, *{user.first_name}*! 👋\n\n"
        f"📚 Bu — *Test Bot*.\n\n"
        f"Pastdagi tugmalardan birini nlang:\\n\\n"
        f"© 2026 — Developed by Badrou Xp"
    )
    await update.message.reply_text(
        text,
        reply_markup=main_menu_kb(user.id),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Srt a quiz — works in both private and group chats."""
    user = update.effective_user
    chat = update.effective_chat
    db.upsert_user(user.id, user.username or "", user.first_name or "", user.last_name or "")

    ste = context.chat_data.get("test")
    if ste:
        await update.message.reply_text(
            "⚠️ يوجد اختبار نشط بالفعل في هذه المحادثة. لإيقافه أرسل /cancel."
        )
        return

    await _show_subject_picker(update.message, _is_group(chat))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 *Yordam*\n\n"
        "/srt — 🏠 القائمة الرئيسية\n"
        "/quiz — بدء اختبار جديد (يعمل في المجموعات أيضاً)\n"
        "/cancel — إلغاء الاختبار الحالي\n\n"
        "يمكنك اختيار الوقت المخصص لكل سؤال من ⚙️ *⚙️ الإعدادات*' (10/15/20/30 ثانية).\n\n"
        "👥 *وضع المجموعة:* أضف البوت إلى المجموعة وأرسل /quiz — يمكن لجميع الأعضاء المشاركة."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_group(update.effective_chat):
        await update.message.reply_text("Guruhda menyu yo'q. /quiz yuboring.")
        return
    user = update.effective_user
    await update.message.reply_text(
        "🏠 القائمة الرئيسية:",
        reply_markup=main_menu_kb(user.id),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat = update.effective_chat
    ste = context.chat_data.get("test")

    if not ste:
        await update.message.reply_text("Hozir faol test yo'q.")
        return

    if _is_group(chat):
        if ste.get("srter_user_id") != user_id:
            try:
                member = await context.bot.get_chat_member(chat.id, user_id)
                if member.stus not in ("administrator", "creator"):
                    await update.message.reply_text(
                        "⛔ يمكن فقط لمن بدأ الاختبار أو مشرف المجموعة إيقافه."
                    )
                    return
            except Exception:
                await update.message.reply_text("⛔ تعذر التحقق من الصلاحيات.")
                return

    tol = len(ste["questions"])
    stopped_early = ste["idx"] < tol
    await _finalize(context, context.chat_data, show_results=True, stopped_early=stopped_early)


# ---------- menu callbacks ----------

async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    da = q.data.split(":", 1)[1]
    user = update.effective_user

    if da == "home":
        await q.edit_message_text(
            "🏠 القائمة الرئيسية:",
            reply_markup=main_menu_kb(user.id),
        )
    elif da == "srt":
        await _show_subjects(q)
    elif da == "my":
        await _show_my_results(q, user.id)
    elif da == "top":
        await _show_top(q)
    elif da == "settings":
        await _show_settings(q, user.id)
    elif da == "about":
        await q.edit_message_text(
            "ℹ️ *ℹ️ حول البوت*\n\n"
            "Bu bot turli fanlar bo'yicha test topshirish uchun yaratilgan.\n\n"
            "• الوقت المخصص لكل سؤال ⚙️ *⚙️ الإعدادات*' (10/15/20/30 s)\n"
            "• يتم حفظ النتائج وعرض ترتيب أفضل النتائج.\n"
            "• يمكن للمشرفين إضافة مواد جديدة.",
            reply_markup=back_kb(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def _show_settings(q, user_id: int) -> None:
    current = db.get_user_timer(user_id)
    rows = []
    for s in db.VALID_TIMERS:
        mark = "  ✅" if s == current else ""
        rows.append([InlineKeyboardButton(f"⏱ {s} ثانية{mark}", callback_data=f"timer:{s}")])
    rows.append([InlineKeyboardButton("⬅️ 🏠 القائمة الرئيسية", callback_data="menu:home")])
    await q.edit_message_text(
        f"⚙️ *⚙️ الإعدادات*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Har savolga ajratilgan vaqt: *{current} ثانية*\n\n"
        f"Bu sozlama keyingi testlarda qo'llaniladi.\n"
        f"في اختبار المجموعة، تُستخدم إعدادات الشخص الذي بدأ الاختبار.",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_timer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    try:
        seconds = int(cq.data.split(":", 1)[1])
    except ValueError:
        await cq.answer("Noto'g'ri qiymat")
        return
    if seconds not in db.VALID_TIMERS:
        await cq.answer("Noto'g'ri qiymat")
        return
    user_id = update.effective_user.id
    db.upsert_user(
        user_id,
        update.effective_user.username or "",
        update.effective_user.first_name or "",
        update.effective_user.last_name or "",
    )
    db.set_user_timer(user_id, seconds)
    await cq.answer(f"✅ الوقت {seconds} ثانيةga تم ضبطه")
    await _show_settings(cq, user_id)


async def _show_subjects(q) -> None:
    subjects = list_subjects()
    if not subjects:
        await q.edit_message_text(
            "⚠️ لا توجد مواضيع متاحة حالياً. انتظر حتى يضيف المشرف مواد.",
            reply_markup=back_kb(),
        )
        return
    rows = []
    for name, path in subjects:
        rows.append([InlineKeyboardButton(f"📘 {name}", callback_data=f"subj:{path.stem}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")])
    await q.edit_message_text(
        "📚 📚 اختر موضوعاً:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    stem = q.data.split(":", 1)[1]
    path = SUBJECTS_DIR / f"{stem}.txt"
    if not path.exists():
        await q.edit_message_text("❌ لم يتم العثور على المادة.", reply_markup=back_kb())
        return
    questions = load_subject(path)
    if not questions:
        await q.edit_message_text("❌ لا توجد أسئلة في هذه المادة.", reply_markup=back_kb())
        return

    display_name = stem.replace("_", " ").replace("-", " ").title()
    user_timer = db.get_user_timer(update.effective_user.id)
    text = (
        f"📘 *{display_name}*\n\n"
        f"📝 عدد الأسئلة: *{len(questions)} *\n"
        f"⏱ الوقت لكل سؤال: *{user_timer} ثانية*\n"
        f"🔀 السؤالlar sodifiy rtibda beriladi\n\n"
        f"Tayyormisiz?"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، ابدأ", callback_data=f"begin:{stem}")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="menu:srt")],
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ---------- test flow (native Telegram quiz polls; private + group) ----------

# Telegram limits
POLL_QUESTION_MAX = 290
POLL_OPTION_MAX = 95
POLL_EXPLANATION_MAX = 195


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _cancel_timer_jobs(ste: dict) -> None:
    for job in ste.get("timer_jobs", []):
        try:
            job.schedule_removal()
        except Exception:
            pass
    ste["timer_jobs"] = []


def _new_participant(user) -> dict:
    return {
        "name": user.first_name or "User",
        "username": user.username or "",
        "user_id": user.id,
        "correct": 0,
        "wrong": 0,
        "skipped": 0,
        "attempt_id": None,
        "answered_q": set(),
    }


async def on_begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    await cq.answer()
    user = update.effective_user
    chat = cq.message.chat
    stem = cq.data.split(":", 1)[1]
    path = SUBJECTS_DIR / f"{stem}.txt"
    questions = load_subject(path)
    if not questions:
        await cq.edit_message_text("❌ تعذر تحميل الأسئلة.")
        return

    await _cancel_current_test(context)

    random.shuffle(questions)
    display_name = stem.replace("_", " ").replace("-", " ").title()
    is_group = _is_group(chat)
    timer_seconds = db.get_user_timer(user.id)

    context.chat_data["test"] = {
        "subject": display_name,
        "subject_stem": stem,
        "questions": questions,
        "idx": 0,
        "chat_id": chat.id,
        "is_group": is_group,
        "srter_user_id": user.id,
        "srter_name": user.first_name or "",
        "timer_seconds": timer_seconds,
        "participants": {},
        "current_poll_id": None,
        "current_q_idx": -1,
        "current_poll_msg_id": None,
        "timer_jobs": [],
    }

    mode_line = (
        "👥 *وضع المجموعة:* يمكن لجميع الأعضاء المشاركة\n"
        if is_group
        else ""
    )
    try:
        await cq.edit_message_text(
            f"🚀 *بدأ الاختبار!*\n\n"
            f"📘 المادة: *{display_name}*\n"
            f"📝 الأسئلة: *{len(questions)} *\n"
            f"⏱ الوقت لكل سؤال: *{timer_seconds} ثانية*\n"
            f"{mode_line}\n"
            f"To'xtish: /cancel",
            parse_mode=ParseMode.MARKDOWN,
        )
    except BadRequest:
        pass

    await _send_quiz(context, context.chat_data)


def _control_kb_active() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏸ إيقاف مؤقت", callback_data="ctl:pause"),
        InlineKeyboardButton("⛔ إنهاء", callback_data="ctl:stop"),
    ]])


def _control_kb_paused() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ متابعة", callback_data="ctl:resume"),
        InlineKeyboardButton("⛔ إنهاء", callback_data="ctl:stop"),
    ]])


async def _send_quiz(context: ContextTypes.DEFAULT_TYPE, chat_da: dict) -> None:
    ste = chat_da.get("test")
    if not ste:
        return
    if ste.get("paused"):
        return
    idx = ste["idx"]
    if idx >= len(ste["questions"]):
        await _finish_test(context, chat_da)
        return

    q = ste["questions"][idx]
    tol = len(ste["questions"])
    chat_id = ste["chat_id"]

    # Shuffle option order so the correct answer isn't always at the same spot
    shuffled = list(q.options)
    random.shuffle(shuffled)
    ste["current_options"] = shuffled
    options = [_truncate(t, POLL_OPTION_MAX) for _, t in shuffled]
    try:
        correct_idx = next(i for i, (letter, _) in enumerate(shuffled) if letter == q.correct)
    except StopIteration:
        correct_idx = 0

    header = f"❓ السؤال {idx + 1}/{tol}"
    full_q = f"{header}\n\n{q.text.strip()}"

    if len(full_q) > POLL_QUESTION_MAX:
        await context.bot.send_message(chat_id=chat_id, text=full_q)
        poll_question = "Javobni nlang:"
    else:
        poll_question = full_q

    explanation = _truncate(f"✅ صحيح: {shuffled[correct_idx][1]}", POLL_EXPLANATION_MAX)

    # Clean up buttons on previous poll (if still showing Pause/Stop)
    prev_msg_id = ste.get("current_poll_msg_id")
    if prev_msg_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=prev_msg_id, reply_markup=None
            )
        except Exception:
            pass

    timer_seconds = ste.get("timer_seconds", TIMEOUT_PER_QUESTION)

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=poll_question,
        options=options,
        type=Poll.QUIZ,
        correct_option_id=correct_idx,
        is_anonymous=False,
        open_period=timer_seconds,
        explanation=explanation,
        reply_markup=_control_kb_active(),
    )

    prev = ste.get("current_poll_id")
    if prev:
        context.application.bot_data.get("poll_chat", {}).pop(prev, None)

    ste["current_poll_id"] = msg.poll.id
    ste["current_q_idx"] = idx
    ste["current_poll_msg_id"] = msg.message_id

    context.application.bot_data.setdefault("poll_chat", {})[msg.poll.id] = chat_id

    _cancel_timer_jobs(ste)
    job = context.job_queue.run_once(
        _on_quiz_timer_end,
        when=timer_seconds + 1,
        data={"poll_id": msg.poll.id, "q_idx": idx, "chat_id": chat_id},
        name=f"timer_end_{chat_id}_{idx}",
    )
    ste["timer_jobs"].append(job)


async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pa = update.poll_answer
    if not pa:
        return

    poll_chat_map = context.application.bot_data.get("poll_chat", {})
    chat_id = poll_chat_map.get(pa.poll_id)
    if not chat_id:
        return
    chat_da = context.application.chat_data[chat_id]
    ste = chat_da.get("test")
    if not ste or ste.get("current_poll_id") != pa.poll_id:
        return
    if ste.get("paused"):
        return

    q_idx = ste["current_q_idx"]
    q = ste["questions"][q_idx]
    user = pa.user

    chosen_idx = pa.option_ids[0] if pa.option_ids else None
    # Map shuffled index back to the original option letter
    shuffled = ste.get("current_options") or q.options
    chosen_letter = (
        shuffled[chosen_idx][0]
        if chosen_idx is not None and chosen_idx < len(shuffled)
        else None
    )

    db.upsert_user(user.id, user.username or "", user.first_name or "", user.last_name or "")

    p = ste["participants"].get(user.id)
    if p is None:
        p = _new_participant(user)
        p["attempt_id"] = db.start_attempt(user.id, ste["subject"], len(ste["questions"]))
        ste["participants"][user.id] = p

    if q_idx in p["answered_q"]:
        return
    p["answered_q"].add(q_idx)

    is_correct = db.record_answer(p["attempt_id"], q.number, chosen_letter, q.correct)
    if is_correct:
        p["correct"] += 1
    else:
        p["wrong"] += 1

    # In private chat, auto-advance once the (only) user has answered.
    if not ste["is_group"]:
        _cancel_timer_jobs(ste)
        if ste["idx"] + 1 >= len(ste["questions"]):
            rget = _finish_job
        else:
            rget = _next_quiz_job
        ste["idx"] += 1
        ste["current_poll_id"] = None
        job = context.job_queue.run_once(
            rget,
            when=2.0,
            data={"chat_id": chat_id},
            name=f"adv_{chat_id}_{q_idx}",
        )
        ste["timer_jobs"].append(job)


async def _on_quiz_timer_end(context: ContextTypes.DEFAULT_TYPE) -> None:
    da = context.job.data
    chat_id = da["chat_id"]
    poll_id = da["poll_id"]
    q_idx = da["q_idx"]
    chat_da = context.application.chat_data[chat_id]
    ste = chat_da.get("test")
    if not ste:
        return
    if ste.get("paused"):
        return
    if ste.get("current_poll_id") != poll_id:
        return
    if ste.get("current_q_idx") != q_idx:
        return

    # If nobody answered this question, auto-pause the test
    any_answered = any(q_idx in p["answered_q"] for p in ste["participants"].values())
    if not any_answered:
        ste["paused"] = True
        msg_id = ste.get("current_poll_msg_id")
        if msg_id:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=msg_id, reply_markup=_control_kb_paused()
                )
            except Exception:
                pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏸ *لم يُجب أحد — تم إيقاف الاختبار مؤقتاً.*\n\n"
                f"Joriy savol: *{q_idx + 1} / {len(ste['questions'])}*\n\n"
                f"▶️ *متابعة* للمتابعة، أو\n"
                f"⛔ *إنهاء* لحفظ النتائج."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # At least one answer received — mark non-answerers as skipped, advance
    q = ste["questions"][q_idx]
    for uid, p in ste["participants"].items():
        if q_idx not in p["answered_q"]:
            db.record_answer(p["attempt_id"], q.number, None, q.correct)
            p["skipped"] += 1
            p["answered_q"].add(q_idx)

    context.application.bot_data.get("poll_chat", {}).pop(poll_id, None)
    ste["idx"] += 1
    ste["current_poll_id"] = None

    if ste["idx"] >= len(ste["questions"]):
        await _finalize(context, chat_da, show_results=True)
    else:
        await _send_quiz(context, chat_da)


async def _next_quiz_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    chat_da = context.application.chat_data[chat_id]
    if "test" not in chat_da:
        return
    await _send_quiz(context, chat_da)


async def _finish_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    chat_da = context.application.chat_data[chat_id]
    if "test" not in chat_da:
        return
    await _finish_test(context, chat_da)


async def _finalize(
    context: ContextTypes.DEFAULT_TYPE,
    chat_da: dict,
    show_results: bool = True,
    stopped_early: bool = False,
) -> None:
    """Tear down a running test, save attempts, and (optionally) show results."""
    ste = chat_da.pop("test", None)
    if not ste:
        return
    _cancel_timer_jobs(ste)

    pid = ste.get("current_poll_id")
    if pid:
        context.application.bot_data.get("poll_chat", {}).pop(pid, None)

    chat_id = ste["chat_id"]
    msg_id = ste.get("current_poll_msg_id")
    if msg_id:
        try:
            await context.bot.stop_poll(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=msg_id, reply_markup=None
            )
        except Exception:
            pass

    for uid, p in ste["participants"].items():
        try:
            if p.get("attempt_id"):
                db.finish_attempt(p["attempt_id"])
        except Exception:
            pass

    if not show_results:
        return

    tol = len(ste["questions"])
    if ste["is_group"]:
        await _send_group_results(context, chat_id, ste, tol, stopped_early)
    else:
        await _send_private_results(context, chat_id, ste, tol, stopped_early)


# Keep a short alias for backward compatibility within this module
async def _finish_test(context, chat_da):
    await _finalize(context, chat_da, show_results=True)


async def _send_private_results(context, chat_id, ste, tol, stopped_early: bool = False) -> None:
    if not ste["participants"]:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ تم إيقاف الاختبار (لم تجب عن أي سؤال.)."
        )
        return
    _, p = next(iter(ste["participants"].items()))
    correct = p["correct"]
    wrong = p["wrong"]
    skipped = p["skipped"]
    answered_tol = correct + wrong + skipped
    pct = (correct / tol * 100) if tol else 0

    if pct >= 90:
        grade, emoji = "🏆 ممتاز!", "🎊"
    elif pct >= 75:
        grade, emoji = "🥇 نتيجة جيدة", "🎉"
    elif pct >= 60:
        grade, emoji = "🥈 مقبول", "👍"
    elif pct >= 40:
        grade, emoji = "🥉 O'rcha", "💪"
    else:
        grade, emoji = "📕 حاول مرة أخرى", "📚"

    title = "تم إيقاف الاختبار" if stopped_early else "انتهى الاختبار!"
    early_line = (
        f"⚠️ Er to'xtildi: *{answered_tol}/{tol}* سؤال تمت الإجابة عنه\n\n"
        if stopped_early
        else ""
    )

    rank_line = ""
    try:
        rows = db.top_results(subject=ste["subject"], limit=10000)
        for i, r in enumerate(rows, 1):
            if r["user_id"] == p["user_id"]:
                rank_line = f"🏅 ترتيبك الحالي: *{i}* / {len(rows)}\n"
                break
    except Exception:
        pass

    best_line = ""
    try:
        best = db.best_attempt(p["user_id"], subject=ste["subject"])
        if best:
            best_pct = (best["correct"] / best["total"] * 100) if best["total"] else 0
            if best["correct"] > correct:
                best_line = f"⭐ أفضل نتيجة لك: *{best['correct']}/{best['total']}* ({best_pct:.0f}%)\n"
    except Exception:
        pass

    text = (
        f"{emoji} *{title}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📘 المادة: *{ste['subject']}*\n"
        f"{early_line}"
        f"📊 النتيجة: *{correct}/{tol}* ({pct:.1f}%)\n\n"
        f"✅ صحيح: {correct}\n"
        f"❌ خطأ: {wrong}\n"
        f"⏭ تم تخطيه: {skipped}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{rank_line}"
        f"{best_line}"
        f"{grade}"
    )
    stem = ste.get("subject_stem", "")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Qay urinish", callback_data=f"begin:{stem}")],
        [InlineKeyboardButton("🏆 🏆 المتصدرون", callback_data="menu:top")],
        [InlineKeyboardButton("🏠 🏠 القائمة الرئيسية", callback_data="menu:home")],
    ])
    await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
    )


async def _send_group_results(context, chat_id, ste, tol, stopped_early: bool = False) -> None:
    parts = ste["participants"]
    if not parts:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🏁 *تم إيقاف الاختبار*\n\n📘 المادة: *{ste['subject']}*\n\n🤷 لم يشارك أحد.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    ranked = sorted(
        parts.values(),
        key=lambda p: (p["correct"], -(p["wrong"] + p["skipped"])),
        reverse=True,
    )
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 100

    title = "تم إيقاف الاختبار" if stopped_early else "انتهى الاختبار!"
    answered_q = ste["idx"] if stopped_early else tol
    progress_line = (
        f"⚠️ Er to'xtildi: *{answered_q}/{tol}* savol o'tildi"
        if stopped_early
        else f"📝 الأسئلة: *{tol}*"
    )

    lines = [
        f"🏁 *{title}*",
        f"━━━━━━━━━━━━━━━━━━",
        f"📘 المادة: *{ste['subject']}*",
        f"{progress_line}",
        f"👥 المشاركون: *{len(ranked)}*",
        f"━━━━━━━━━━━━━━━━━━",
        f"",
        f"🏆 *🏆 المتصدرون:*",
    ]
    for i, p in enumerate(ranked):
        pct = (p["correct"] / tol * 100) if tol else 0
        name = (p["name"] or "User").replace("*", "").replace("_", " ").replace("`", "")
        lines.append(
            f"{medals[i]} *{name}* — {p['correct']}/{tol} ({pct:.0f}%)"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 العب مرة أخرى", callback_data=f"begin:{ste['subject_stem']}")],
    ])
    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cancel_current_test(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Silent cleanup — used when a new test is srting and we want to clear
    any leftover ste without showing a results screen."""
    ste = context.chat_data.get("test")
    if not ste:
        return False
    await _finalize(context, context.chat_data, show_results=False)
    return True


# ---------- pause / resume / stop controls ----------

async def _can_control(update: Update, context: ContextTypes.DEFAULT_TYPE, ste: dict) -> bool:
    user_id = update.effective_user.id
    if not ste.get("is_group"):
        return True
    if user_id == ste.get("srter_user_id"):
        return True
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.stus in ("administrator", "creator")
    except Exception:
        return False


async def on_control(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    ste = context.chat_data.get("test")
    if not ste:
        await cq.answer("Faol test yo'q.", show_alert=True)
        return
    if not await _can_control(update, context, ste):
        await cq.answer(
            "⛔ Bu tugmani faqat testni boshlagan kishi yoki guruh admini bosishi mumkin.",
            show_alert=True,
        )
        return

    action = cq.data.split(":", 1)[1]
    if action == "pause":
        await _pause_quiz(context, ste, cq)
    elif action == "resume":
        await _resume_quiz(context, ste, cq)
    elif action == "stop":
        await _stop_quiz(context, ste, cq)


async def _pause_quiz(context: ContextTypes.DEFAULT_TYPE, ste: dict, cq) -> None:
    if ste.get("paused"):
        await cq.answer("Allaqachon pauza")
        return
    await cq.answer("⏸ إيقاف مؤقت qilindi")
    ste["paused"] = True
    _cancel_timer_jobs(ste)

    chat_id = ste["chat_id"]
    msg_id = ste.get("current_poll_msg_id")
    if msg_id:
        try:
            await context.bot.stop_poll(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=msg_id, reply_markup=_control_kb_paused()
            )
        except Exception:
            pass

    idx = ste["idx"]
    tol = len(ste["questions"])
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏸ *Test pauza qilindi*\n\n"
            f"Joriy savol: *{idx + 1} / {tol}*\n"
            f"متابعة uchun yuqoridagi ▶️ tugmasini bosing."
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _resume_quiz(context: ContextTypes.DEFAULT_TYPE, ste: dict, cq) -> None:
    if not ste.get("paused"):
        await cq.answer()
        return
    await cq.answer("▶️ Davom etmoqda...")
    ste["paused"] = False
    # Remove control buttons from the paused poll
    msg_id = ste.get("current_poll_msg_id")
    if msg_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=ste["chat_id"], message_id=msg_id, reply_markup=None
            )
        except Exception:
            pass
    await _send_quiz(context, context.chat_data)


async def _stop_quiz(context: ContextTypes.DEFAULT_TYPE, ste: dict, cq) -> None:
    await cq.answer("⛔ تم إيقاف الاختبار")
    tol = len(ste["questions"])
    stopped_early = ste["idx"] < tol
    await _finalize(context, context.chat_data, show_results=True, stopped_early=stopped_early)


# ---------- my results / top ----------

async def _show_my_results(q, user_id: int) -> None:
    attempts = db.get_user_attempts(user_id, limit=10)
    subj_sts = db.user_subject_stats(user_id)
    if not attempts and not subj_sts:
        await q.edit_message_text(
            "📊 📊 ليست لديك نتائج اختبارات بعد.",
            reply_markup=back_kb(),
        )
        return

    lines = ["📊 *📊 نتائجك*", "━━━━━━━━━━━━━━━━━━"]

    if subj_sts:
        lines.append("\n*📚 📚 النتائج حسب المواد:*\n")
        for s in subj_sts:
            best = (s["best_pct"] or 0) * 100
            avg = (s["avg_pct"] or 0) * 100
            lines.append(
                f"• *{s['subject']}*\n"
                f"   عدد المحاولات: *{s['attempts']}* · أفضل نتيجة: *{best:.0f}%* · O'rcha: *{avg:.0f}%*"
            )

    finished = [a for a in attempts if a["finished_at"]]
    if finished:
        lines.append("\n*🕐 آخر المحاولات:*\n")
        for a in finished:
            pct = (a["correct"] / a["total"] * 100) if a["total"] else 0
            date = a["srted_at"][:10]
            lines.append(
                f"• `{date}` — {a['subject']}: {a['correct']}/{a['total']} ({pct:.0f}%)"
            )

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=back_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _show_top(q) -> None:
    """Top-level leaderboard menu: pick a subject or view overall."""
    subjects = list_subjects()
    rows = [[InlineKeyboardButton("🌐 🌐 عام (جميع المواد)", callback_data="top:all")]]
    for name, path in subjects:
        rows.append([InlineKeyboardButton(f"📘 {name}", callback_data=f"top:{path.stem}")])
    rows.append([InlineKeyboardButton("⬅️ 🏠 القائمة الرئيسية", callback_data="menu:home")])
    await q.edit_message_text(
        "🏆 *🏆 لوحة المتصدرين*\n\n📚 أي مادة تريد عرض نتائجها؟",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    arg = q.data.split(":", 1)[1]
    user_id = update.effective_user.id

    if arg == "all":
        rows = db.top_results(limit=15)
        title = "🌐 🏆 لوحة المتصدرين العامة (جميع المواد)"
        subject_filter = None
    else:
        path = SUBJECTS_DIR / f"{arg}.txt"
        display_name = arg.replace("_", " ").replace("-", " ").title()
        rows = db.top_results(subject=display_name, limit=15)
        title = f"📘 {display_name} — المتصدرون"
        subject_filter = display_name

    if not rows:
        await q.edit_message_text(
            f"{title}\n\n🤷 لا توجد نتائج حالياً.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ رجوع", callback_data="menu:top")
            ]]),
        )
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 12
    lines = [f"🏆 *{title}*", "━━━━━━━━━━━━━━━━━━", ""]

    user_rank = None
    user_entry = None
    for i, r in enumerate(rows, 1):
        pct = (r["correct"] / r["total"] * 100) if r["total"] else 0
        name = r["first_name"] or (f"@{r['username']}" if r["username"] else f"User {r['user_id']}")
        name = name.replace("*", "").replace("_", " ").replace("`", "")
        marker = "👉 " if r["user_id"] == user_id else ""
        subj_suffix = "" if subject_filter else f" · {r['subject']}"
        lines.append(
            f"{medals[i-1] if i <= 15 else '·'} {marker}*{name}*  —  "
            f"{r['correct']}/{r['total']} ({pct:.0f}%){subj_suffix}"
        )
        if r["user_id"] == user_id and user_rank is None:
            user_rank = i
            user_entry = r

    # If user not in top 15, find their best rank
    if user_rank is None:
        all_rows = db.top_results(subject=subject_filter, limit=10000)
        for i, r in enumerate(all_rows, 1):
            if r["user_id"] == user_id:
                user_rank = i
                user_entry = r
                break

    lines.append("━━━━━━━━━━━━━━━━━━")
    if user_entry:
        pct = (user_entry["correct"] / user_entry["total"] * 100) if user_entry["total"] else 0
        lines.append(f"🎯 أفضل ترتيب حققته:  *{user_rank}*  ({user_entry['correct']}/{user_entry['total']}, {pct:.0f}%)")
    else:
        lines.append("🎯 لم تشارك في أي اختبار بعد.")

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ 🏆 المتصدرون", callback_data="menu:top"),
            InlineKeyboardButton("🏠 🏠 القائمة الرئيسية", callback_data="menu:home"),
        ]]),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- admin ----------

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ ليس لديك صلاحيات المشرف.")
        return
    await update.message.reply_text(
        f"👑 *👨‍💻 لوحة الإدارة*\nUser ID: `{user.id}`",
        reply_markup=_admin_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stistika", callback_data="admin:sts")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin:users")],
        [InlineKeyboardButton("🏆 أفضل النتائج", callback_data="admin:top")],
        [InlineKeyboardButton("📚 قائمة المواد", callback_data="admin:subjects")],
        [InlineKeyboardButton("➕ إضافة مادة جديدة", callback_data="admin:addsubject")],
        [InlineKeyboardButton("📢 إرسال رسالة للجميع", callback_data="admin:broadcast")],
        [InlineKeyboardButton("⬅️ 🏠 القائمة الرئيسية", callback_data="menu:home")],
    ])


async def on_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    user = update.effective_user
    if not is_admin(user.id):
        await q.answer("⛔ لست مشرفاً.", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]

    if action == "home":
        await q.edit_message_text(
            f"👑 *👨‍💻 لوحة الإدارة*\nUser ID: `{user.id}`",
            reply_markup=_admin_kb(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "sts":
        s = db.sts_overview()
        per_subj = "\n".join(f"  • {n}: {c} mar" for n, c in s["per_subject"]) or "  (لا يوجد)"
        text = (
            f"📊 *Stistika*\n\n"
            f"👥 المستخدمون: *{s['users']}*\n"
            f"📝 الاختبارات المكتملة: *{s['attempts']}*\n"
            f"📈 O'rcha ball: *{s['avg_score']*100:.1f}%*\n\n"
            f"*📚 النتائج حسب المواد:*\n{per_subj}"
        )
        await q.edit_message_text(text, reply_markup=_admin_back_kb(), parse_mode=ParseMode.MARKDOWN)

    elif action == "users":
        users = db.list_users(limit=30)
        if not users:
            await q.edit_message_text("المستخدمون yo'q.", reply_markup=_admin_back_kb())
            return
        lines = ["👥 *So'nggi foydalanuvchilar (30 si):*", "اضغط على الزر لعرض الملف الشخصي.\n"]
        rows = []
        for u in users:
            uname = f"@{u['username']}" if u["username"] else "—"
            lines.append(f"• `{u['user_id']}` · {u['first_name'] or '—'} · {uname} · testlar: {u['attempts_count']}")
            label = f"👤 {u['first_name'] or u['user_id']} ({u['attempts_count']})"
            rows.append([InlineKeyboardButton(label[:60], callback_data=f"admin:u:{u['user_id']}")])
        rows.append([InlineKeyboardButton("⬅️ 👨‍💻 لوحة الإدارة", callback_data="admin:home")])
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action.srtswith("u:"):
        try:
            rget_uid = int(action.split(":", 1)[1])
        except ValueError:
            await q.edit_message_text("❌ معرّف المستخدم غير صحيح.", reply_markup=_admin_back_kb())
            return
        await _admin_user_profile(q, rget_uid)

    elif action == "top":
        rows = db.top_results(limit=20)
        if not rows:
            await q.edit_message_text("Natijalar yo'q.", reply_markup=_admin_back_kb())
            return
        lines = ["🏆 *أفضل النتائج (20):*\n"]
        for i, r in enumerate(rows, 1):
            pct = (r["correct"] / r["total"] * 100) if r["total"] else 0
            name = r["first_name"] or (f"@{r['username']}" if r["username"] else f"#{r['user_id']}")
            lines.append(f"{i}. *{name}* · {r['subject']} · {r['correct']}/{r['total']} ({pct:.0f}%)")
        await q.edit_message_text("\n".join(lines), reply_markup=_admin_back_kb(), parse_mode=ParseMode.MARKDOWN)

    elif action == "subjects":
        subjects = list_subjects()
        if not subjects:
            await q.edit_message_text("Fanlar yo'q.", reply_markup=_admin_back_kb())
            return
        rows = []
        lines = ["📚 *المواد:*", "اضغط على الزر للإدارة.\n"]
        for name, path in subjects:
            try:
                qs = load_subject(path)
                lines.append(f"• *{name}* — {len(qs)}  savol")
            except Exception as e:
                lines.append(f"• *{name}* — ❌ خطأ: {e}")
            rows.append([InlineKeyboardButton(f"📘 {name}", callback_data=f"admin:sub:{path.stem}")])
        rows.append([InlineKeyboardButton("⬅️ 👨‍💻 لوحة الإدارة", callback_data="admin:home")])
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action.srtswith("sub:"):
        stem = action.split(":", 1)[1]
        await _admin_subject_card(q, stem)

    elif action.srtswith("subdel:"):
        stem = action.split(":", 1)[1]
        await _admin_subject_delete_confirm(q, stem)

    elif action.srtswith("subdelyes:"):
        stem = action.split(":", 1)[1]
        await _admin_subject_delete_execute(q, stem)

    elif action.srtswith("subren:"):
        stem = action.split(":", 1)[1]
        context.user_data["awaiting_subject_rename"] = stem
        await q.edit_message_text(
            f"✏️ أرسل اسم الملف الجديد (kengaytmasiz, faqat lotin harflari va `_`).\n\n"
            f"Hozirgi: `{stem}`\n\n"
            f"إلغاء uchun /cancel yuboring.",
            reply_markup=_admin_back_kb(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "addsubject":
        context.user_data["awaiting_subject_file"] = True
        await q.edit_message_text(
            "📤 إضافة مادة جديدة uchun *.txt* faylni yuboring.\n\n"
            "Fayl formati: oddiy matn, savol raqami `1.` bilan boshlanadi, "
            "to'g'ri javob `+` belgisi bilan belgilanadi.\n\n"
            "Fayl nomi fan nomi sifatida ishlatiladi (masalan `matematika.txt`).",
            reply_markup=_admin_back_kb(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "broadcast":
        context.user_data["awaiting_broadcast"] = True
        await q.edit_message_text(
            "📢 اكتب الرسالة التي تريد إرسالها للجميع.\n\n"
            "إلغاء uchun /cancel yuboring.",
            reply_markup=_admin_back_kb(),
        )


def _admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ 👨‍💻 لوحة الإدارة", callback_data="admin:home")],
    ])


async def _admin_subject_card(q, stem: str) -> None:
    path = SUBJECTS_DIR / f"{stem}.txt"
    if not path.exists():
        await q.edit_message_text("❌ لم يتم العثور على المادة.", reply_markup=_admin_back_kb())
        return
    display_name = stem.replace("_", " ").replace("-", " ").title()
    try:
        qs = load_subject(path)
        count_line = f"📝 عدد الأسئلة: *{len(qs)}*"
    except Exception as e:
        count_line = f"❌ خطأ: {e}"
    size_kb = path.st().st_size / 1024
    text = (
        f"📘 *{display_name}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Fayl: `{path.name}`\n"
        f"Hajmi: {size_kb:.1f} KB\n"
        f"{count_line}"
    )
    rows = [
        [InlineKeyboardButton("✏️ Qay nomlash", callback_data=f"admin:subren:{stem}")],
        [InlineKeyboardButton("🗑 حذف", callback_data=f"admin:subdel:{stem}")],
        [InlineKeyboardButton("⬅️ قائمة المواد", callback_data="admin:subjects")],
    ]
    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _admin_subject_delete_confirm(q, stem: str) -> None:
    path = SUBJECTS_DIR / f"{stem}.txt"
    if not path.exists():
        await q.edit_message_text("❌ لم يتم العثور على المادة.", reply_markup=_admin_back_kb())
        return
    display_name = stem.replace("_", " ").replace("-", " ").title()
    rows = [
        [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"admin:subdelyes:{stem}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"admin:sub:{stem}")],
    ]
    await q.edit_message_text(
        f"⚠️ *{display_name}* هل تريد حذف المادة؟\n\n"
        f"Fayl o'chiriladi. Eski testlardagi natijalar saqlanib qoladi.",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _admin_subject_delete_execute(q, stem: str) -> None:
    path = SUBJECTS_DIR / f"{stem}.txt"
    if not path.exists():
        await q.edit_message_text("❌ تم حذف المادة بالفعل.", reply_markup=_admin_back_kb())
        return
    try:
        path.unlink()
    except Exception as e:
        await q.edit_message_text(f"❌ خطأ: {e}", reply_markup=_admin_back_kb())
        return
    display_name = stem.replace("_", " ").replace("-", " ").title()
    await q.edit_message_text(
        f"🗑 *{display_name}* تم الحذف.",
        reply_markup=_admin_back_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _admin_user_profile(q, user_id: int) -> None:
    u = db.get_user(user_id)
    if not u:
        await q.edit_message_text("❌ لم يتم العثور على المستخدم.", reply_markup=_admin_back_kb())
        return
    attempts = db.get_user_attempts(user_id, limit=10)
    subj_sts = db.user_subject_stats(user_id)

    uname = f"@{u['username']}" if u["username"] else "—"
    full_name = " ".join(filter(None, [u["first_name"], u["last_name"]])) or "—"
    lines = [
        f"👤 *ملف المستخدم*",
        f"━━━━━━━━━━━━━━━",
        f"ID: `{user_id}`",
        f"Ism: *{full_name}*",
        f"Username: {uname}",
        f"Ro'yxatdan o'tgan: `{u['created_at'][:10]}`",
        f"Timer: {u['timer_seconds']} s",
    ]

    if subj_sts:
        lines.append("\n*📚 📚 النتائج حسب المواد:*")
        for s in subj_sts:
            best = (s["best_pct"] or 0) * 100
            avg = (s["avg_pct"] or 0) * 100
            lines.append(
                f"• {s['subject']} — المحاولات: *{s['attempts']}*, "
                f"أفضل نتيجة: *{best:.0f}%*, المعدل: *{avg:.0f}%*"
            )
    else:
        lines.append("\n_Tugatilgan testlar yo'q._")

    finished = [a for a in attempts if a["finished_at"]]
    if finished:
        lines.append("\n*🕐 آخر المحاولات:*")
        for a in finished[:5]:
            pct = (a["correct"] / a["total"] * 100) if a["total"] else 0
            date = a["srted_at"][:10]
            lines.append(f"• `{date}` · {a['subject']}: {a['correct']}/{a['total']} ({pct:.0f}%)")

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ المستخدمون", callback_data="admin:users")],
            [InlineKeyboardButton("🏠 👨‍💻 لوحة الإدارة", callback_data="admin:home")],
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle file upload from admin to add a new subject."""
    user = update.effective_user
    if not is_admin(user.id) or not context.user_data.get("awaiting_subject_file"):
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("❌ يُقبل فقط ملف بصيغة .txt.")
        return

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in doc.file_name)
    dest = SUBJECTS_DIR / safe_name
    if dest.exists():
        await update.message.reply_text(f"⚠️ `{safe_name}` موجود بالفعل. احذفه أولاً أو غيّر اسمه.")
        return

    file = await doc.get_file()
    await file.download_to_drive(custom_path=dest)

    # validate
    try:
        qs = load_subject(dest)
        if not qs:
            dest.unlink()
            await update.message.reply_text("❌ لم يتم العثور على أسئلة في الملف. تم حذف الملف.")
            return
        context.user_data["awaiting_subject_file"] = False
        await update.message.reply_text(
            f"✅ *{safe_name}* تمت الإضافة.\nعدد الأسئلة: *{len(qs)}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb(user.id),
        )
    except Exception as e:
        dest.unlink(missing_ok=True)
        await update.message.reply_text(f"❌ تعذرت قراءة الملف: {e}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text — used for admin broadcast and rename flow."""
    user = update.effective_user
    text = update.message.text or ""

    old_stem = context.user_data.get("awaiting_subject_rename")
    if old_stem and is_admin(user.id):
        context.user_data["awaiting_subject_rename"] = None
        new_stem = "".join(c if c.isalnum() or c in "_-" else "_" for c in text.strip())
        if not new_stem:
            await update.message.reply_text("❌ الاسم فارغ. تم الإلغاء.", reply_markup=main_menu_kb(user.id))
            return
        old_path = SUBJECTS_DIR / f"{old_stem}.txt"
        new_path = SUBJECTS_DIR / f"{new_stem}.txt"
        if not old_path.exists():
            await update.message.reply_text("❌ لم يتم العثور على الملف القديم.", reply_markup=main_menu_kb(user.id))
            return
        if new_path.exists():
            await update.message.reply_text(f"⚠️ `{new_stem}.txt` allaqachon mavjud.", parse_mode=ParseMode.MARKDOWN)
            return
        try:
            old_path.rename(new_path)
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ: {e}")
            return
        await update.message.reply_text(
            f"✅ Qay nomlandi: `{old_stem}.txt` → `{new_stem}.txt`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb(user.id),
        )
        return

    if context.user_data.get("awaiting_broadcast") and is_admin(user.id):
        context.user_data["awaiting_broadcast"] = False
        user_ids = db.all_user_ids()
        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 *Xabar*\n\n{text}", parse_mode=ParseMode.MARKDOWN)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await update.message.reply_text(
            f"✅ تم الإرسال: {sent}\n❌ خطأ: {failed}",
            reply_markup=main_menu_kb(user.id),
        )
        return

    # default: show menu
    await update.message.reply_text("🏠 القائمة الرئيسية:", reply_markup=main_menu_kb(user.id))

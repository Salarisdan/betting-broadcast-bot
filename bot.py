import logging
import json
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters
)
from config import TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, ALLOWED_USER_IDS, GROUPS_FILE
import anthropic

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── States ───────────────────────────────────────────────────────────────────
(
    MAIN_MENU,
    ADD_GROUP_WAIT_ID, ADD_GROUP_WAIT_NAME,
    BROADCAST_SELECT_GROUPS, BROADCAST_WAIT_APK,
    BROADCAST_WAIT_TEXT, BROADCAST_WAIT_COUNT,
    BROADCAST_WAIT_TIME, BROADCAST_CONFIRM,
    GENERATE_WAIT_TOPIC,
) = range(10)

# ─── Groups storage ───────────────────────────────────────────────────────────

def load_groups() -> dict:
    if os.path.exists(GROUPS_FILE):
        with open(GROUPS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_groups(groups: dict):
    with open(GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

# ─── Auth ─────────────────────────────────────────────────────────────────────

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

# ─── Claude generation ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — эксперт по беттингу и гемблингу. Пишешь посты для Telegram.
Правила:
- Живой текст с характером, не сухой
- Умеренные эмодзи
- 150–300 слов
- Разные форматы: советы, стратегии, факты, психология, аналитика
- Не рекламируй конкретные бренды
- Только русский язык
- Без хэштегов"""

async def generate_post_text(topic: str) -> str:
    message = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Напиши пост на тему: {topic}"}]
    )
    return message.content[0].text

# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Создать рассылку", callback_data="broadcast_start")],
        [InlineKeyboardButton("👥 Управление группами", callback_data="groups_menu")],
        [InlineKeyboardButton("✏️ Сгенерировать пост", callback_data="generate_menu")],
    ])

def groups_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить группу", callback_data="group_add")],
        [InlineKeyboardButton("➖ Удалить группу", callback_data="group_delete_list")],
        [InlineKeyboardButton("📋 Список групп", callback_data="group_list")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
    ])

def select_groups_kb(groups: dict, selected: set):
    buttons = []
    for gid, gname in groups.items():
        check = "✅" if gid in selected else "☑️"
        buttons.append([InlineKeyboardButton(f"{check} {gname}", callback_data=f"sel_{gid}")])
    buttons.append([
        InlineKeyboardButton("🔙 Назад", callback_data="main_menu"),
        InlineKeyboardButton("✔️ Далее", callback_data="sel_done"),
    ])
    return InlineKeyboardMarkup(buttons)

def delete_groups_kb(groups: dict):
    buttons = [[InlineKeyboardButton(f"🗑 {gname}", callback_data=f"del_{gid}")] for gid, gname in groups.items()]
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="groups_menu")])
    return InlineKeyboardMarkup(buttons)

def back_kb(target="main_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=target)]])

def confirm_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Запустить", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Отмена", callback_data="main_menu"),
    ]])

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_bd(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "broadcast" not in context.user_data:
        context.user_data["broadcast"] = {}
    return context.user_data["broadcast"]

# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    context.user_data.clear()
    await update.message.reply_text("👋 Бот для рассылки постов в группы", reply_markup=main_menu_kb())
    return MAIN_MENU

# ─── Callbacks: navigation ────────────────────────────────────────────────────

async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.edit_message_text("Главное меню:", reply_markup=main_menu_kb())
    return MAIN_MENU

async def cb_groups_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("👥 Управление группами:", reply_markup=groups_menu_kb())
    return MAIN_MENU

async def cb_group_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    groups = load_groups()
    if not groups:
        text = "Нет добавленных групп"
    else:
        lines = [f"• {name}  (`{gid}`)" for gid, name in groups.items()]
        text = "📋 Группы:\n\n" + "\n".join(lines)
    await q.edit_message_text(text, reply_markup=groups_menu_kb(), parse_mode="Markdown")
    return MAIN_MENU

# ─── Callbacks: add group ─────────────────────────────────────────────────────

async def cb_group_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Перешли любое сообщение из нужной группы/канала.\n\n"
        "Или введи chat_id вручную (например: `-1001234567890` или `@username`):",
        parse_mode="Markdown",
        reply_markup=back_kb("groups_menu")
    )
    return ADD_GROUP_WAIT_ID

async def msg_add_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    chat_id = None
    auto_name = ""

    if update.message.forward_origin:
        origin = update.message.forward_origin
        if hasattr(origin, "chat"):
            chat_id = str(origin.chat.id)
            auto_name = origin.chat.title or origin.chat.username or chat_id

    if not chat_id:
        text = update.message.text.strip() if update.message.text else ""
        if text.lstrip("-").isdigit() or text.startswith("@"):
            chat_id = text
        else:
            await update.message.reply_text("❌ Не распознал. Введи chat_id или @username")
            return ADD_GROUP_WAIT_ID

    context.user_data["new_group_id"] = chat_id
    context.user_data["new_group_auto_name"] = auto_name
    hint = f'\n(или нажми Enter чтобы оставить "{auto_name}")' if auto_name else ""
    await update.message.reply_text(
        f"ID: `{chat_id}`\nВведи название для этой группы:{hint}",
        parse_mode="Markdown",
        reply_markup=back_kb("groups_menu")
    )
    return ADD_GROUP_WAIT_NAME

async def msg_add_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    name = update.message.text.strip() or context.user_data.get("new_group_auto_name", "Без названия")
    gid = context.user_data.get("new_group_id")
    groups = load_groups()
    groups[gid] = name
    save_groups(groups)
    await update.message.reply_text(f"✅ Группа добавлена: *{name}*", parse_mode="Markdown", reply_markup=groups_menu_kb())
    return MAIN_MENU

async def cb_group_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    groups = load_groups()
    if not groups:
        await q.edit_message_text("Нет групп для удаления", reply_markup=groups_menu_kb())
        return MAIN_MENU
    await q.edit_message_text("Выбери группу для удаления:", reply_markup=delete_groups_kb(groups))
    return MAIN_MENU

async def cb_group_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    gid = q.data.replace("del_", "")
    groups = load_groups()
    name = groups.pop(gid, gid)
    save_groups(groups)
    await q.edit_message_text(f"🗑 Удалено: {name}", reply_markup=groups_menu_kb())
    return MAIN_MENU

# ─── Broadcast flow ───────────────────────────────────────────────────────────

async def cb_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    groups = load_groups()
    if not groups:
        await q.edit_message_text("❌ Нет групп. Сначала добавь группы.", reply_markup=groups_menu_kb())
        return MAIN_MENU
    context.user_data["broadcast"] = {"selected": set()}
    await q.edit_message_text("Выбери группы для рассылки:", reply_markup=select_groups_kb(groups, set()))
    return BROADCAST_SELECT_GROUPS

async def cb_select_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    gid = q.data[4:]  # strip "sel_"
    groups = load_groups()
    data = get_bd(context)
    selected = data.get("selected", set())
    selected.discard(gid) if gid in selected else selected.add(gid)
    data["selected"] = selected
    await q.edit_message_reply_markup(reply_markup=select_groups_kb(groups, selected))
    return BROADCAST_SELECT_GROUPS

async def cb_select_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = get_bd(context)
    if not data.get("selected"):
        await q.answer("⚠️ Выбери хотя бы одну группу!", show_alert=True)
        return BROADCAST_SELECT_GROUPS
    await q.edit_message_text(
        "📎 Загрузи APK-файл — отправь его в этот чат:",
        reply_markup=back_kb("broadcast_start")
    )
    return BROADCAST_WAIT_APK

async def msg_receive_apk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Отправь файл (document)")
        return BROADCAST_WAIT_APK
    data = get_bd(context)
    data["apk_file_id"] = doc.file_id
    data["apk_name"] = doc.file_name
    await update.message.reply_text(
        f"✅ Файл принят: `{doc.file_name}`\n\n"
        "✏️ Напиши текст поста.\n\n"
        "Или отправь `/gen <тема>` чтобы сгенерировать через Claude:",
        parse_mode="Markdown"
    )
    return BROADCAST_WAIT_TEXT

async def msg_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = update.message.text.strip()

    if text.lower().startswith("/gen"):
        topic = text[4:].strip() or "беттинг"
        msg = await update.message.reply_text("⏳ Генерирую через Claude...")
        try:
            generated = await generate_post_text(topic)
            get_bd(context)["post_text"] = generated
            await msg.edit_text(
                f"✅ Готово:\n\n{generated}\n\n"
                "Сколько раз отправить в каждую группу? (введи число):"
            )
            return BROADCAST_WAIT_COUNT
        except Exception as e:
            await msg.edit_text(f"❌ Ошибка: {e}")
            return BROADCAST_WAIT_TEXT

    get_bd(context)["post_text"] = text
    await update.message.reply_text("Сколько раз отправить пост в каждую группу? (введи число):")
    return BROADCAST_WAIT_COUNT

async def msg_receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("❌ Введи целое число больше 0")
        return BROADCAST_WAIT_COUNT
    get_bd(context)["count"] = int(text)
    await update.message.reply_text(
        "⏰ Когда отправить?\n\n"
        "• `сейчас`\n"
        "• `через 30м` / `через 2ч`\n"
        "• `14:30` — сегодня в это время\n"
        "• `2025-06-01 10:00` — конкретная дата",
        parse_mode="Markdown"
    )
    return BROADCAST_WAIT_TIME

async def msg_receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = update.message.text.strip().lower()
    delay_seconds = 0
    send_at = None

    try:
        if text == "сейчас":
            delay_seconds = 0
        elif text.startswith("через "):
            rest = text[6:].strip()
            if rest.endswith("м"):
                delay_seconds = int(rest[:-1]) * 60
            elif rest.endswith("ч"):
                delay_seconds = int(rest[:-1]) * 3600
            else:
                raise ValueError
        elif len(text) == 5 and ":" in text:
            now = datetime.now()
            send_at = now.replace(hour=int(text[:2]), minute=int(text[3:]), second=0, microsecond=0)
            if send_at <= now:
                send_at += timedelta(days=1)
            delay_seconds = (send_at - now).total_seconds()
        else:
            send_at = datetime.strptime(text, "%Y-%m-%d %H:%M")
            delay_seconds = (send_at - datetime.now()).total_seconds()
            if delay_seconds < 0:
                await update.message.reply_text("❌ Дата в прошлом")
                return BROADCAST_WAIT_TIME
    except Exception:
        await update.message.reply_text("❌ Не распознал. Попробуй: `сейчас`, `через 30м`, `14:30`", parse_mode="Markdown")
        return BROADCAST_WAIT_TIME

    data = get_bd(context)
    data["delay_seconds"] = max(0, delay_seconds)
    groups = load_groups()
    selected_names = [groups.get(gid, gid) for gid in data["selected"]]

    if delay_seconds == 0:
        time_str = "немедленно"
    elif send_at:
        time_str = send_at.strftime("%d.%m.%Y %H:%M")
    elif delay_seconds < 3600:
        time_str = f"через {int(delay_seconds // 60)} мин"
    else:
        time_str = f"через {int(delay_seconds // 3600)} ч"

    preview = data["post_text"][:300] + ("..." if len(data["post_text"]) > 300 else "")
    summary = (
        f"📋 *Подтверди рассылку:*\n\n"
        f"👥 Группы: {', '.join(selected_names)}\n"
        f"📄 Файл: {data.get('apk_name', '—')}\n"
        f"🔁 Раз в каждую группу: {data['count']}\n"
        f"⏰ Время: {time_str}\n\n"
        f"📝 Текст:\n{preview}"
    )
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=confirm_kb())
    return BROADCAST_CONFIRM

async def cb_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = get_bd(context)
    delay = data.get("delay_seconds", 0)
    chat_id = update.effective_chat.id
    bot = context.bot

    if delay > 0:
        mins = int(delay // 60)
        await q.edit_message_text(f"✅ Рассылка запланирована. Отправлю через {mins} мин.")
        await asyncio.sleep(delay)
    else:
        await q.edit_message_text("🚀 Отправляю...")

    groups = load_groups()
    errors = []
    sent = 0

    for gid in data["selected"]:
        for _ in range(data["count"]):
            try:
                await bot.send_document(
                    chat_id=gid,
                    document=data["apk_file_id"],
                    caption=data["post_text"],
                )
                sent += 1
                await asyncio.sleep(1)
            except Exception as e:
                gname = groups.get(gid, gid)
                errors.append(f"• {gname}: {e}")
                logger.error(f"Send error to {gid}: {e}")

    result = f"✅ Отправлено: {sent} сообщений"
    if errors:
        result += "\n\n❌ Ошибки:\n" + "\n".join(errors)

    await bot.send_message(chat_id=chat_id, text=result, reply_markup=main_menu_kb())
    context.user_data.clear()
    return MAIN_MENU

# ─── Generate standalone ──────────────────────────────────────────────────────

async def cb_generate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✏️ Введи тему для генерации поста:",
        reply_markup=back_kb("main_menu")
    )
    return GENERATE_WAIT_TOPIC

async def msg_generate_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    topic = update.message.text.strip()
    msg = await update.message.reply_text("⏳ Генерирую через Claude...")
    try:
        text = await generate_post_text(topic)
        await msg.edit_text(f"✅ Готово:\n\n{text}", reply_markup=main_menu_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}", reply_markup=main_menu_kb())
    return MAIN_MENU

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            CallbackQueryHandler(cb_groups_menu, pattern="^groups_menu$"),
            CallbackQueryHandler(cb_group_list, pattern="^group_list$"),
            CallbackQueryHandler(cb_group_add, pattern="^group_add$"),
            CallbackQueryHandler(cb_group_delete_list, pattern="^group_delete_list$"),
            CallbackQueryHandler(cb_group_delete, pattern="^del_"),
            CallbackQueryHandler(cb_broadcast_start, pattern="^broadcast_start$"),
            CallbackQueryHandler(cb_generate_menu, pattern="^generate_menu$"),
            CallbackQueryHandler(cb_select_group, pattern="^sel_(?!done)"),
            CallbackQueryHandler(cb_select_done, pattern="^sel_done$"),
            CallbackQueryHandler(cb_confirm_yes, pattern="^confirm_yes$"),
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
                CallbackQueryHandler(cb_groups_menu, pattern="^groups_menu$"),
                CallbackQueryHandler(cb_group_list, pattern="^group_list$"),
                CallbackQueryHandler(cb_group_add, pattern="^group_add$"),
                CallbackQueryHandler(cb_group_delete_list, pattern="^group_delete_list$"),
                CallbackQueryHandler(cb_group_delete, pattern="^del_"),
                CallbackQueryHandler(cb_broadcast_start, pattern="^broadcast_start$"),
                CallbackQueryHandler(cb_generate_menu, pattern="^generate_menu$"),
            ],
            ADD_GROUP_WAIT_ID: [
                MessageHandler(filters.ALL & ~filters.COMMAND, msg_add_group_id),
                CallbackQueryHandler(cb_groups_menu, pattern="^groups_menu$"),
            ],
            ADD_GROUP_WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_add_group_name),
                CallbackQueryHandler(cb_groups_menu, pattern="^groups_menu$"),
            ],
            BROADCAST_SELECT_GROUPS: [
                CallbackQueryHandler(cb_select_group, pattern="^sel_(?!done)"),
                CallbackQueryHandler(cb_select_done, pattern="^sel_done$"),
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            ],
            BROADCAST_WAIT_APK: [
                MessageHandler(filters.Document.ALL, msg_receive_apk),
                CallbackQueryHandler(cb_broadcast_start, pattern="^broadcast_start$"),
            ],
            BROADCAST_WAIT_TEXT: [
                MessageHandler(filters.TEXT, msg_receive_text),
                CallbackQueryHandler(cb_broadcast_start, pattern="^broadcast_start$"),
            ],
            BROADCAST_WAIT_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_receive_count),
            ],
            BROADCAST_WAIT_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_receive_time),
            ],
            BROADCAST_CONFIRM: [
                CallbackQueryHandler(cb_confirm_yes, pattern="^confirm_yes$"),
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            ],
            GENERATE_WAIT_TOPIC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_generate_topic),
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            CallbackQueryHandler(cb_groups_menu, pattern="^groups_menu$"),
            CallbackQueryHandler(cb_group_list, pattern="^group_list$"),
            CallbackQueryHandler(cb_group_add, pattern="^group_add$"),
            CallbackQueryHandler(cb_group_delete_list, pattern="^group_delete_list$"),
            CallbackQueryHandler(cb_group_delete, pattern="^del_"),
            CallbackQueryHandler(cb_broadcast_start, pattern="^broadcast_start$"),
            CallbackQueryHandler(cb_generate_menu, pattern="^generate_menu$"),
            CallbackQueryHandler(cb_select_group, pattern="^sel_(?!done)"),
            CallbackQueryHandler(cb_select_done, pattern="^sel_done$"),
            CallbackQueryHandler(cb_confirm_yes, pattern="^confirm_yes$"),
        ],
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
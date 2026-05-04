import logging
import json
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import ChatMemberUpdated
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters,
    ChatMemberHandler
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
    BROADCAST_WAIT_TIME, BROADCAST_PICK_DATE,
    BROADCAST_PICK_HOUR, BROADCAST_PICK_MINUTE, BROADCAST_CONFIRM,
    GENERATE_WAIT_TOPIC,
) = range(13)

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

def schedule_time_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Сейчас", callback_data="time_now")],
        [
            InlineKeyboardButton("🕒 Через 30 минут", callback_data="time_in_30m"),
            InlineKeyboardButton("🕐 Через 1 час", callback_data="time_in_1h"),
        ],
        [InlineKeyboardButton("📅 Выбрать дату и время", callback_data="time_pick_date")],
        [InlineKeyboardButton("🔙 В главное меню", callback_data="main_menu")],
    ])

def pick_date_kb(days: int = 14):
    now = datetime.now()
    rows = []
    row = []
    for i in range(days):
        day = now + timedelta(days=i)
        day_key = day.strftime("%Y%m%d")
        if i == 0:
            label = f"Сегодня {day.strftime('%d.%m')}"
        elif i == 1:
            label = f"Завтра {day.strftime('%d.%m')}"
        else:
            label = day.strftime("%d.%m")
        row.append(InlineKeyboardButton(label, callback_data=f"pick_day_{day_key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="time_back")])
    return InlineKeyboardMarkup(rows)

def pick_hour_kb(day_key: str):
    rows = []
    row = []
    for hour in range(24):
        row.append(InlineKeyboardButton(f"{hour:02d}", callback_data=f"pick_hour_{day_key}_{hour:02d}"))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 К выбору даты", callback_data="time_pick_date")])
    return InlineKeyboardMarkup(rows)

def pick_minute_kb(day_key: str, hour: str):
    minutes = ["00", "10", "20", "30", "40", "50"]
    rows = [[InlineKeyboardButton(m, callback_data=f"pick_min_{day_key}_{hour}_{m}") for m in minutes]]
    rows.append([InlineKeyboardButton("🔙 К выбору часа", callback_data=f"pick_day_{day_key}")])
    return InlineKeyboardMarkup(rows)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_bd(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "broadcast" not in context.user_data:
        context.user_data["broadcast"] = {}
    return context.user_data["broadcast"]

def build_broadcast_summary(data: dict, groups: dict, delay_seconds: float, send_at: datetime | None) -> str:
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
    return (
        f"📋 *Подтверди рассылку:*\n\n"
        f"👥 Группы: {', '.join(selected_names)}\n"
        f"📄 Файл: {data.get('apk_name', '—')}\n"
        f"🔁 Раз в каждую группу: {data['count']}\n"
        f"⏰ Время: {time_str}\n\n"
        f"📝 Текст:\n{preview}"
    )

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

# ─── Auto-save group when bot is added ───────────────────────────────────────

async def on_bot_added_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically save group when bot is added as member/admin."""
    result: ChatMemberUpdated = update.my_chat_member
    if not result:
        return
    new_status = result.new_chat_member.status
    # bot was added (member or admin)
    if new_status in ("member", "administrator"):
        chat = result.chat
        if chat.type in ("group", "supergroup", "channel"):
            chat_id = str(chat.id)
            name = chat.title or chat.username or chat_id
            groups = load_groups()
            if chat_id not in groups:
                groups[chat_id] = name
                save_groups(groups)
                logger.info(f"Auto-saved group: {name} ({chat_id})")
                # Notify all allowed users
                for uid in ALLOWED_USER_IDS:
                    try:
                        await context.bot.send_message(
                            uid,
                            f"✅ Бот добавлен в группу и она сохранена:\n{name}"
                        )
                    except Exception:
                        pass

async def cb_group_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Как добавить группу:\n\n"
        "1️⃣ Добавь бота в группу/канал как администратора — "
        "группа сохранится автоматически.\n\n"
        "2️⃣ Или перешли сюда любое сообщение из группы/канала:\n"
        "   (нажми на сообщение → Переслать → выбери этот бот)",
        reply_markup=back_kb("groups_menu")
    )
    return ADD_GROUP_WAIT_ID

async def msg_add_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    chat_id = None
    name = ""

    # Forwarded message from a channel/group
    if update.message.forward_origin:
        origin = update.message.forward_origin
        if hasattr(origin, "chat"):
            chat_id = str(origin.chat.id)
            name = origin.chat.title or origin.chat.username or chat_id

    # @username or numeric id typed manually
    if not chat_id and update.message.text:
        text = update.message.text.strip()
        if text.startswith("@") or text.lstrip("-").isdigit():
            # Try to get chat info
            try:
                chat = await context.bot.get_chat(text)
                chat_id = str(chat.id)
                name = chat.title or chat.username or chat_id
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Не удалось получить инфо о чате: {e}\n"
                    "Убедись что бот уже состоит в этой группе."
                )
                return ADD_GROUP_WAIT_ID

    if not chat_id:
        await update.message.reply_text(
            "❌ Не распознал. Перешли сообщение из группы или отправь @username"
        )
        return ADD_GROUP_WAIT_ID

    groups = load_groups()
    groups[chat_id] = name
    save_groups(groups)
    await update.message.reply_text(f"✅ Группа добавлена: {name}", reply_markup=groups_menu_kb())
    return MAIN_MENU

async def msg_add_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    name = update.message.text.strip() or context.user_data.get("new_group_auto_name", "Без названия")
    gid = context.user_data.get("new_group_id")
    groups = load_groups()
    groups[gid] = name
    save_groups(groups)
    await update.message.reply_text(f"✅ Группа добавлена: {name}", reply_markup=groups_menu_kb())
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
        "⏰ *Шаг 5/5: выбери время отправки*\n\n"
        "Подсказка: нажми быстрый вариант или выбери точную дату и время.",
        parse_mode="Markdown",
        reply_markup=schedule_time_kb(),
    )
    return BROADCAST_WAIT_TIME

async def cb_time_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = get_bd(context)
    data["delay_seconds"] = 0
    groups = load_groups()
    summary = build_broadcast_summary(data, groups, 0, None)
    await q.edit_message_text(summary, parse_mode="Markdown", reply_markup=confirm_kb())
    return BROADCAST_CONFIRM

async def cb_time_in_30m(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    delay = 30 * 60
    data = get_bd(context)
    data["delay_seconds"] = delay
    groups = load_groups()
    summary = build_broadcast_summary(data, groups, delay, None)
    await q.edit_message_text(summary, parse_mode="Markdown", reply_markup=confirm_kb())
    return BROADCAST_CONFIRM

async def cb_time_in_1h(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    delay = 60 * 60
    data = get_bd(context)
    data["delay_seconds"] = delay
    groups = load_groups()
    summary = build_broadcast_summary(data, groups, delay, None)
    await q.edit_message_text(summary, parse_mode="Markdown", reply_markup=confirm_kb())
    return BROADCAST_CONFIRM

async def cb_time_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "⏰ *Шаг 5/5: выбери время отправки*\n\n"
        "Подсказка: нажми быстрый вариант или выбери точную дату и время.",
        parse_mode="Markdown",
        reply_markup=schedule_time_kb(),
    )
    return BROADCAST_WAIT_TIME

async def cb_time_pick_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📅 *Выбор даты*\n\n"
        "Подсказка: сначала выбери день, затем час и минуты.",
        parse_mode="Markdown",
        reply_markup=pick_date_kb(),
    )
    return BROADCAST_PICK_DATE

async def cb_pick_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    day_key = q.data.replace("pick_day_", "")
    get_bd(context)["picked_day"] = day_key
    await q.edit_message_text(
        f"🕐 *Выбор часа*\n\nДата: {day_key[6:8]}.{day_key[4:6]}.{day_key[:4]}\n"
        "Подсказка: выбери час в 24-часовом формате.",
        parse_mode="Markdown",
        reply_markup=pick_hour_kb(day_key),
    )
    return BROADCAST_PICK_HOUR

async def cb_pick_hour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, _, day_key, hour = q.data.split("_", 3)
    data = get_bd(context)
    data["picked_day"] = day_key
    data["picked_hour"] = hour
    await q.edit_message_text(
        f"🕓 *Выбор минут*\n\nДата: {day_key[6:8]}.{day_key[4:6]}.{day_key[:4]}\n"
        f"Час: {hour}\n"
        "Подсказка: выбери минуты (шаг 10 мин).",
        parse_mode="Markdown",
        reply_markup=pick_minute_kb(day_key, hour),
    )
    return BROADCAST_PICK_MINUTE

async def cb_pick_minute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, _, day_key, hour, minute = q.data.split("_", 4)

    send_at = datetime.strptime(f"{day_key} {hour}:{minute}", "%Y%m%d %H:%M")
    delay_seconds = (send_at - datetime.now()).total_seconds()
    if delay_seconds < 0:
        await q.answer("⚠️ Это время уже прошло. Выбери другое.", show_alert=True)
        return BROADCAST_PICK_MINUTE

    data = get_bd(context)
    data["delay_seconds"] = delay_seconds
    groups = load_groups()
    summary = build_broadcast_summary(data, groups, delay_seconds, send_at)
    await q.edit_message_text(summary, parse_mode="Markdown", reply_markup=confirm_kb())
    return BROADCAST_CONFIRM

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
                CallbackQueryHandler(cb_time_now, pattern="^time_now$"),
                CallbackQueryHandler(cb_time_in_30m, pattern="^time_in_30m$"),
                CallbackQueryHandler(cb_time_in_1h, pattern="^time_in_1h$"),
                CallbackQueryHandler(cb_time_pick_date, pattern="^time_pick_date$"),
                CallbackQueryHandler(cb_time_back, pattern="^time_back$"),
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            ],
            BROADCAST_PICK_DATE: [
                CallbackQueryHandler(cb_pick_day, pattern="^pick_day_"),
                CallbackQueryHandler(cb_time_back, pattern="^time_back$"),
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            ],
            BROADCAST_PICK_HOUR: [
                CallbackQueryHandler(cb_pick_hour, pattern="^pick_hour_"),
                CallbackQueryHandler(cb_time_pick_date, pattern="^time_pick_date$"),
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
            ],
            BROADCAST_PICK_MINUTE: [
                CallbackQueryHandler(cb_pick_minute, pattern="^pick_min_"),
                CallbackQueryHandler(cb_pick_day, pattern="^pick_day_"),
                CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
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
            CallbackQueryHandler(cb_time_now, pattern="^time_now$"),
            CallbackQueryHandler(cb_time_in_30m, pattern="^time_in_30m$"),
            CallbackQueryHandler(cb_time_in_1h, pattern="^time_in_1h$"),
            CallbackQueryHandler(cb_time_pick_date, pattern="^time_pick_date$"),
            CallbackQueryHandler(cb_time_back, pattern="^time_back$"),
            CallbackQueryHandler(cb_pick_day, pattern="^pick_day_"),
            CallbackQueryHandler(cb_pick_hour, pattern="^pick_hour_"),
            CallbackQueryHandler(cb_pick_minute, pattern="^pick_min_"),
            CallbackQueryHandler(cb_confirm_yes, pattern="^confirm_yes$"),
        ],
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    # Auto-save groups when bot is added to them (outside conversation)
    app.add_handler(ChatMemberHandler(on_bot_added_to_chat, ChatMemberHandler.MY_CHAT_MEMBER))
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
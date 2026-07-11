import os
import logging
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8961851146:AAHBvUqRr1DPcWU4K7mvkkRnXdNwGFruNpY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7096804451"))
# -----------------

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# === БАЗА ДАННЫХ SQLite ===
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        consented INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message_text TEXT,
        message_id INTEGER,
        date TEXT
    )
""")
conn.commit()

def db_get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def db_set_consent(user_id, username, full_name):
    cursor.execute("""
        INSERT INTO users (user_id, username, full_name, consented, banned)
        VALUES (?, ?, ?, 1, 0)
        ON CONFLICT(user_id) DO UPDATE SET consented=1, username=excluded.username, full_name=excluded.full_name
    """, (user_id, username, full_name))
    conn.commit()

def db_ban_user(user_id):
    cursor.execute("UPDATE users SET banned = 1, consented = 0 WHERE user_id = ?", (user_id,))
    conn.commit()

def db_unban_user(user_id):
    cursor.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
    conn.commit()

def db_get_all_users():
    cursor.execute("SELECT user_id, username, full_name, consented, banned FROM users ORDER BY user_id")
    return cursor.fetchall()

def db_save_message(user_id, text, msg_id):
    from datetime import datetime
    cursor.execute("""
        INSERT INTO messages (user_id, message_text, message_id, date)
        VALUES (?, ?, ?, ?)
    """, (user_id, text[:500], msg_id, datetime.now().isoformat()))
    conn.commit()

def db_get_user_messages(user_id, limit=5):
    cursor.execute(
        "SELECT message_text, date FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    return cursor.fetchall()

# === ТЕКСТ ПРАВИЛ ===
RULES_TEXT = """
При обращении в этого бота, вы соглашаетесь что:

1) Я не чиню компы, не показываю переустановки системы и не удаляю вирусы
2) За отправку мусора, флуд, просьб удалить вирусы, починить комп, переустановить систему, или обращение не по теме вы можете быть заблокированы
3) В случае блокировки разбана не будет

Для согласия с правилами напишите: 
<b>Я согласен с правилами</b>
"""

BANNED_PHRASES = [
    "почини комп", "починить комп", "чинить комп", "почини компьютер",
    "переустанови систему", "переустановить систему", "переустановка системы",
    "удали вирус", "удалить вирус", "удаление вируса", "вирус",
    "помоги с компом", "комп не работает", "компьютер сломался",
    "ноутбук не включается", "синий экран",
]

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def is_banned_message(text: str) -> bool:
    text_lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in text_lower:
            return True
    return False

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# Состояния для ConversationHandler (ответ пользователю)
REPLY_TEXT = range(1)
reply_target = {}  # user_id -> target_user_id

# === КОМАНДА /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Если админ — показываем админ-панель
    if is_admin(user_id):
        return await admin_panel(update, context)

    # Проверка на бан
    user_data = db_get_user(user_id)
    if user_data and user_data[4] == 1:  # banned
        await update.message.reply_text("❌ Вы заблокированы. Разбан не предусмотрен.")
        return

    # Показываем правила
    await update.message.reply_text(RULES_TEXT, parse_mode="HTML")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает админ-панель"""
    keyboard = [
        [InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton("🚫 Забанить пользователя", callback_data="admin_ban")],
        [InlineKeyboardButton("✅ Разбанить пользователя", callback_data="admin_unban")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = "🛡 <b>Админ-панель</b>\n\nВыберите действие:"
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)

# === ОБРАБОТКА СООБЩЕНИЙ ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    username = user.username or f"{user.first_name} {user.last_name or ''}".strip()
    full_name = f"{user.first_name} {user.last_name or ''}".strip()

    # === АДМИН: команда /reply <user_id> ===
    if is_admin(user_id) and text.startswith("/reply"):
        parts = text.split()
        if len(parts) >= 2:
            try:
                target_id = int(parts[1])
                reply_target[user_id] = target_id
                await update.message.reply_text(
                    f"✏️ Теперь напиши ответ пользователю {target_id}.\n"
                    "После отправки ответ придёт ему в бота.\n"
                    "Отмена: /cancel"
                )
                return REPLY_TEXT
            except ValueError:
                await update.message.reply_text("❌ Неверный ID.")
                return

    # === АДМИН: обычное сообщение ===
    if is_admin(user_id):
        await update.message.reply_text(
            "Используй админ-панель: /start\n"
            "Чтобы ответить пользователю: /reply <user_id>"
        )
        return

    # === ОБЫЧНЫЙ ПОЛЬЗОВАТЕЛЬ ===
    user_data = db_get_user(user_id)

    # Забанен
    if user_data and user_data[4] == 1:
        return

    # Проверка на бан-слова
    if is_banned_message(text):
        db_ban_user(user_id)
        await update.message.reply_text(
            "🚫 Вы заблокированы за нарушение правил.\n"
            "Причина: сообщение содержит запрещённую тему.\n"
            "В случае блокировки разбана не будет."
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔴 ЗАБЛОКИРОВАН @{user.username or 'нет'} ({user_id})\n"
                 f"Имя: {full_name}\n"
                 f"Текст: {text[:200]}"
        )
        return

    # Проверка согласия
    if not user_data or user_data[3] == 0:  # not consented
        if text.strip().lower() == "я согласен с правилами":
            db_set_consent(user_id, username, full_name)

            # Создаём клавиатуру с информацией
            keyboard = [
                [InlineKeyboardButton("👤 Профиль", url=f"tg://user?id={user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✅ <b>НОВЫЙ ПОЛЬЗОВАТЕЛЬ</b>\n"
                     f"ID: <code>{user_id}</code>\n"
                     f"Username: @{user.username or 'нет'}\n"
                     f"Имя: {full_name}",
                parse_mode="HTML",
                reply_markup=reply_markup
            )

            await update.message.reply_text(
                "✅ Спасибо! Правила приняты. Теперь вы можете писать сообщения.\n"
                "Ваше сообщение будет отправлено администратору."
            )
        else:
            await update.message.reply_text(
                "❌ Вы не приняли правила.\n\n" + RULES_TEXT,
                parse_mode="HTML"
            )
        return

    # Согласие есть — сохраняем и пересылаем админу
    db_save_message(user_id, text, update.message.message_id)

    # Пересылаем сообщение
    await update.message.forward(chat_id=ADMIN_ID)

    # Кнопки для админа
    keyboard = [
        [
            InlineKeyboardButton("🚫 Забанить", callback_data=f"ban_{user_id}"),
            InlineKeyboardButton("✅ Разбанить", callback_data=f"unban_{user_id}"),
        ],
        [InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{user_id}")],
        [InlineKeyboardButton("👤 Профиль", url=f"tg://user?id={user_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📬 <b>Сообщение от</b> @{user.username or 'нет'} | {full_name}\n"
             f"ID: <code>{user_id}</code>\n\n"
             f"Текст: {text[:300]}",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

    await update.message.reply_text("✅ Сообщение отправлено администратору!")


# === ОБРАБОТКА КНОПОК (CALLBACK) ===
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.edit_message_text("❌ Доступ запрещён.")
        return

    data = query.data

    # ---- Админ-панель навигация ----
    if data == "admin_users":
        users = db_get_all_users()
        if not users:
            await query.edit_message_text("📭 Нет пользователей.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")],
            ]))
            return

        text = "👥 <b>Все пользователи:</b>\n\n"
        for uid, uname, fname, consented, banned in users:
            status = "✅" if consented else ("🚫" if banned else "⬜")
            text += f"{status} <code>{uid}</code> | @{uname or 'нет'} | {fname}\n"

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        await query.edit_message_text(text[:4000], parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "admin_back":
        return await admin_panel(update, context)

    elif data == "admin_ban":
        users = db_get_all_users()
        keyboard = []
        for uid, uname, fname, consented, banned in users:
            if not banned:
                keyboard.append([InlineKeyboardButton(
                    f"🚫 {fname[:20]} (@{uname or 'нет'})", callback_data=f"ban_{uid}"
                )])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
        
        if not keyboard[:-1]:
            await query.edit_message_text("✅ Все пользователи уже забанены.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("Выберите пользователя для бана:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "admin_unban":
        users = db_get_all_users()
        keyboard = []
        for uid, uname, fname, consented, banned in users:
            if banned:
                keyboard.append([InlineKeyboardButton(
                    f"✅ {fname[:20]} (@{uname or 'нет'})", callback_data=f"unban_{uid}"
                )])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
        
        if not keyboard[:-1]:
            await query.edit_message_text("✅ Нет забаненных пользователей.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("Выберите пользователя для разбана:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # ---- Бан / Разбан / Ответ ----
    elif data.startswith("ban_"):
        target_id = int(data.split("_")[1])
        db_ban_user(target_id)
        await query.edit_message_text(f"✅ Пользователь <code>{target_id}</code> забанен.", parse_mode="HTML")
        
        # Уведомление пользователю
        try:
            await context.bot.send_message(chat_id=target_id, text="🚫 Вы были заблокированы администратором.")
        except:
            pass

    elif data.startswith("unban_"):
        target_id = int(data.split("_")[1])
        db_unban_user(target_id)
        await query.edit_message_text(f"✅ Пользователь <code>{target_id}</code> разбанен.", parse_mode="HTML")
        
        try:
            await context.bot.send_message(chat_id=target_id, text="✅ Вы были разблокированы администратором.")
        except:
            pass

    elif data.startswith("reply_"):
        target_id = int(data.split("_")[1])
        reply_target[user_id] = target_id
        await query.edit_message_text(
            f"✏️ Напиши ответ пользователю <code>{target_id}</code>.\n"
            "После отправки ответ придёт ему в бота.\n"
            "Отмена: /cancel",
            parse_mode="HTML"
        )
        return REPLY_TEXT


# === ОТВЕТ АДМИНА ПОЛЬЗОВАТЕЛЮ ===
async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in reply_target:
        return

    target_id = reply_target[user_id]
    
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"📩 <b>Ответ администратора:</b>\n\n{text}",
            parse_mode="HTML"
        )
        await update.message.reply_text(f"✅ Ответ отправлен пользователю <code>{target_id}</code>.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки: {e}")

    del reply_target[user_id]
    return ConversationHandler.END


# === ОТМЕНА ===
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in reply_target:
        del reply_target[user_id]
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# === HEALTH-CHECK ДЛЯ RENDER ===
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    server = HTTPServer(("0.0.0.0", int(os.getenv("PORT", 10000))), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()


# === ЗАПУСК ===
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler для ответа пользователю
    reply_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply)],
        states={
            REPLY_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(reply_conv, group=1)  # Приоритет ниже
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

import logging
import sqlite3
import datetime
import html
import asyncio
from dateutil import parser
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ===== Логирование =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== Конфигурация =====
TOKEN = "8790618971:AAH4QQ29F5b7JUw7PW-EW4S2xTZXlE2R6eY"          # ваш токен
ADMIN_IDS = [8071127858, 711314367]           # ваш ID (список)

DB_NAME = "tasks.db"
scheduler = AsyncIOScheduler()
app = None

# ===== База данных =====
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  username TEXT,
                  task TEXT,
                  due_date TEXT,
                  chat_id INTEGER,
                  admin_id INTEGER,
                  day_before_sent INTEGER DEFAULT 0,
                  deadline_sent INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

def add_task(user_id, username, task, due_date_str, chat_id, admin_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (user_id, username, task, due_date, chat_id, admin_id, day_before_sent, deadline_sent) "
        "VALUES (?,?,?,?,?,?,0,0)",
        (user_id, username, task, due_date_str, chat_id, admin_id)
    )
    task_id = c.lastrowid
    conn.commit()
    conn.close()
    return task_id

def get_all_tasks():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, user_id, username, task, due_date, chat_id, admin_id, day_before_sent, deadline_sent FROM tasks ORDER BY due_date")
    rows = c.fetchall()
    conn.close()
    return rows

def get_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, user_id, username, task, due_date, chat_id, admin_id, day_before_sent, deadline_sent FROM tasks WHERE id=?", (task_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_tasks_by_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, user_id, username, task, due_date, chat_id, admin_id, day_before_sent, deadline_sent FROM tasks WHERE user_id=? AND deadline_sent=1", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

def mark_day_before_sent(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET day_before_sent=1 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

def mark_deadline_sent(task_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET deadline_sent=1 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

# ===== Отправка напоминаний (с повторными попытками) =====
async def send_day_before_reminder(task_id, retries=3):
    global app
    if app is None:
        logger.error("Application не инициализирован")
        return

    task = get_task(task_id)
    if not task:
        return

    task_id, user_id, username, task_text, due_date_str, chat_id, admin_id, day_before_sent, deadline_sent = task
    if day_before_sent:
        return

    admin_mention = f'<a href="tg://user?id={admin_id}">Админ</a>'
    user_mention = f'<a href="tg://user?id={user_id}">{html.escape(username or "Пользователь")}</a>'
    message_text = (
        f"⏰ Напоминание за сутки: {user_mention}, вам была дана задача:\n"
        f"{html.escape(task_text)}\n"
        f"Дедлайн: {html.escape(due_date_str)}\n"
        f"{admin_mention}, проверьте выполнение."
    )

    for attempt in range(retries):
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                read_timeout=60,
                write_timeout=60
            )
            mark_day_before_sent(task_id)
            logger.info(f"Напоминание за сутки отправлено для задачи {task_id}")
            return
        except Exception as e:
            logger.warning(f"Попытка {attempt+1} отправки напоминания за сутки не удалась: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
            else:
                logger.error(f"Ошибка отправки напоминания за сутки после {retries} попыток: {e}")

async def send_deadline_reminder(task_id, retries=3):
    global app
    if app is None:
        logger.error("Application не инициализирован")
        return

    task = get_task(task_id)
    if not task:
        return

    task_id, user_id, username, task_text, due_date_str, chat_id, admin_id, day_before_sent, deadline_sent = task
    if deadline_sent:
        return

    user_mention = f'<a href="tg://user?id={user_id}">{html.escape(username or "Пользователь")}</a>'
    message_text = (
        f"🚨 Сегодня дедлайн! {user_mention}, ваша задача:\n"
        f"{html.escape(task_text)}\n"
        f"Дедлайн: {html.escape(due_date_str)}\n"
        f"Подтвердите выполнение, написав ++ в ответ на это сообщение (или просто в чат)."
    )

    for attempt in range(retries):
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                read_timeout=60,
                write_timeout=60
            )
            mark_deadline_sent(task_id)
            logger.info(f"Дедлайн-напоминание отправлено для задачи {task_id}")
            return
        except Exception as e:
            logger.warning(f"Попытка {attempt+1} отправки дедлайн-напоминания не удалась: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
            else:
                logger.error(f"Ошибка отправки дедлайн-напоминания после {retries} попыток: {e}")

def schedule_reminders(task_id, due_date_str):
    due_date = parser.parse(due_date_str)
    # Если время не указано, ставим 23:59:59
    if due_date.hour == 0 and due_date.minute == 0 and due_date.second == 0:
        due_date = due_date.replace(hour=23, minute=59, second=59)

    now = datetime.datetime.now()

    # 1. Напоминание за сутки
    day_before = due_date - datetime.timedelta(days=1)
    if day_before > now:
        scheduler.add_job(send_day_before_reminder, 'date', run_date=day_before, args=[task_id])
        logger.info(f"Запланировано напоминание за сутки для задачи {task_id} на {day_before}")
    else:
        scheduler.add_job(send_day_before_reminder, 'date', run_date=now + datetime.timedelta(seconds=5), args=[task_id])
        logger.info(f"Напоминание за сутки для задачи {task_id} будет отправлено немедленно (просрочено)")

    # 2. Напоминание в день дедлайна
    if due_date > now:
        scheduler.add_job(send_deadline_reminder, 'date', run_date=due_date, args=[task_id])
        logger.info(f"Запланировано дедлайн-напоминание для задачи {task_id} на {due_date}")
    else:
        scheduler.add_job(send_deadline_reminder, 'date', run_date=now + datetime.timedelta(seconds=10), args=[task_id])
        logger.info(f"Дедлайн-напоминание для задачи {task_id} будет отправлено немедленно (просрочено)")

# ===== Обработчик подтверждения "++" =====
async def handle_plus_plus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    tasks = get_tasks_by_user(user_id)
    if not tasks:
        await update.message.reply_text("У вас нет активных задачи с сегодняшним дедлайном.")
        return

    for task in tasks:
        task_id = task[0]
        delete_task(task_id)
        logger.info(f"Задача {task_id} удалена по подтверждению от пользователя {user_id}")

    await update.message.reply_text(
        f"✅ {html.escape(username)}, ваша задача выполнена и удалена из списка. Молодец!"
    )

# ===== Команды админа =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для управления задачами.\n"
        "/addtask – добавить задачу (только админ, ответом на сообщение пользователя).\n"
        "/listtasks – список всех задачи (только админ).\n"
        "/help – справка."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "/addtask задача ! дата – добавить задача (ответьте на сообщение исполнителя).\n"
        "  Примеры:\n"
        "  /addtask Подготовить отчёт ! 2026-07-15\n"
        "  /addtask Подготовить отчёт ! 15.07.2026\n"
        "  /addtask Подготовить отчёт ! 15 июля 2026\n"
        "/listtasks – показать все задачи (только админ).\n"
        "/help – эта справка.\n\n"
        "Участники могут подтвердить выполнение задач, написав в чат ++."
    )

async def addtask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для добавления задач.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Эта команда должна быть ответом на сообщение пользователя, которому назначается задача."
        )
        return

    text = update.message.text
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text(
            "Формат: /addtask задача ! дата\n"
            "Пример: /addtask Подготовить отчёт ! 2026-07-15"
        )
        return

    args = parts[1].strip()
    if "!" not in args:
        await update.message.reply_text("Используйте разделитель '!' между задачей и датой.")
        return

    task_part, date_part = args.split("!", 1)
    task_text = task_part.strip()
    date_str = date_part.strip()

    if not task_text or not date_str:
        await update.message.reply_text("Задача и дата не могут быть пустыми.")
        return

    # Парсим дату с поддержкой разных форматов
    try:
        due_date = parser.parse(date_str, fuzzy=True, dayfirst=True)
        # Если время не указано – устанавливаем 23:59:59
        if due_date.hour == 0 and due_date.minute == 0 and due_date.second == 0:
            due_date = due_date.replace(hour=23, minute=59, second=59)
    except Exception as e:
        logger.error(f"Ошибка парсинга даты '{date_str}': {e}")
        await update.message.reply_text(
            "Неверный формат даты. Используйте, например:\n"
            "2026-07-15, 15.07.2026, 15 июля 2026"
        )
        return

    target = update.message.reply_to_message.from_user
    target_id = target.id
    target_username = target.username or target.first_name

    due_date_str = due_date.isoformat()
    chat_id = update.message.chat_id
    admin_id = user_id

    task_id = add_task(target_id, target_username, task_text, due_date_str, chat_id, admin_id)
    schedule_reminders(task_id, due_date_str)

    await update.message.reply_text(
        f"✅ Задача добавлено для {target_username}:\n{task_text}\nДедлайн: {due_date_str}"
    )
    logger.info(f"Задача {task_id} добавлена, дедлайн {due_date_str}")

async def listtasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для просмотра списка задач.")
        return

    tasks = get_all_tasks()
    if not tasks:
        await update.message.reply_text("Задач пока нет.")
        return

    lines = []
    for task in tasks:
        task_id, user_id, username, task_text, due_date_str, chat_id, admin_id, day_before_sent, deadline_sent = task
        status = ""
        if deadline_sent:
            status = "⌛ ожидает подтверждения"
        elif day_before_sent:
            status = "⏳ напоминание отправлено"
        else:
            status = "📅 ожидает"
        lines.append(
            f"#{task_id}: {task_text} (для {username}, дедлайн {due_date_str}) – {status}"
        )
    await update.message.reply_text("\n".join(lines))

# ===== Восстановление при старте =====
async def post_init(application):
    global app
    app = application
    scheduler.start()

    tasks = get_all_tasks()
    for task in tasks:
        task_id, user_id, username, task_text, due_date_str, chat_id, admin_id, day_before_sent, deadline_sent = task
        if not day_before_sent and not deadline_sent:
            schedule_reminders(task_id, due_date_str)
        elif day_before_sent and not deadline_sent:
            due_date = parser.parse(due_date_str)
            if due_date > datetime.datetime.now():
                scheduler.add_job(send_deadline_reminder, 'date', run_date=due_date, args=[task_id])
                logger.info(f"Восстановлено дедлайн-напоминание для задачи {task_id} на {due_date}")
            else:
                scheduler.add_job(send_deadline_reminder, 'date', run_date=datetime.datetime.now() + datetime.timedelta(seconds=5), args=[task_id])
                logger.info(f"Дедлайн-напоминание для задачи {task_id} будет отправлено немедленно (просрочено)")

    logger.info("Планировщик запущен, задачи восстановлены")

# ===== Запуск =====
def main():
    init_db()
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .connect_timeout(60)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("addtask", addtask))
    application.add_handler(CommandHandler("listtasks", listtasks))
    application.add_handler(MessageHandler(filters.Text("++"), handle_plus_plus))

    logger.info("Бот запущен и ожидает сообщения...")
    application.run_polling()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)



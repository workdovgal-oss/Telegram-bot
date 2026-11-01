import telebot
import requests
import os
import threading
import psycopg2
import psycopg2.extras
import json
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
MY_CHAT_ID = os.getenv("MY_CHAT_ID") 
DATABASE_URL = os.getenv("DATABASE_URL")

TZ = ZoneInfo('Europe/Minsk')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# --- ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

# --- ИНИЦИАЛИЗАЦИЯ ТАБЛИЦ ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            text TEXT,
            remind_time TEXT,
            is_sent INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recurring_tasks (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            text TEXT,
            next_run_time TEXT,
            frequency TEXT, 
            interval INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            title TEXT,
            event_date TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            first_name TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS morning_images (
            id SERIAL PRIMARY KEY,
            url TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    cursor.close()
    conn.close()

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БАЗОЙ (КАРТИНКИ) ---
def add_morning_image(url):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO morning_images (url) VALUES (%s)", (url,))
    conn.commit()
    cursor.close()
    conn.close()

def get_random_morning_image():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM morning_images ORDER BY RANDOM() LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else None

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ БЕЗОПАСНОЙ ОТПРАВКИ ФОТО ---
def send_photo_safe(chat_id, image_url, caption, parse_mode='HTML'):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(image_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            bot.send_photo(chat_id, response.content, caption=caption, parse_mode=parse_mode)
        else:
            raise Exception(f"Сервер картинки вернул код {response.status_code}")
            
    except Exception as e:
        print(f"Ошибка загрузки фото: {e}")
        error_caption = caption + "\n\n<i>(Не удалось загрузить картинку. Возможно, ссылка устарела или защищена.)</i>"
        bot.send_message(chat_id, error_caption, parse_mode=parse_mode)

# --- ФУНКЦИИ БАЗЫ ДАННЫХ (ЗАДАЧИ) ---
def add_user_to_db(user_id, first_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, first_name) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET first_name = EXCLUDED.first_name", (user_id, first_name))
    conn.commit()
    cursor.close()
    conn.close()

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name FROM users")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return users

def add_reminder_to_db(user_id, text, remind_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO reminders (user_id, text, remind_time) VALUES (%s, %s, %s)", (user_id, text, remind_time))
    conn.commit()
    cursor.close()
    conn.close()

def add_recurring_to_db(user_id, text, next_run_time, frequency, interval):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO recurring_tasks (user_id, text, next_run_time, frequency, interval) VALUES (%s, %s, %s, %s, %s)", 
        (user_id, text, next_run_time, frequency, interval)
    )
    conn.commit()
    cursor.close()
    conn.close()

def add_event_to_db(user_id, title, event_date):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO events (user_id, title, event_date) VALUES (%s, %s, %s)", (user_id, title, event_date))
    conn.commit()
    cursor.close()
    conn.close()

def delete_all_matching_tasks(user_id, text):
    conn = get_db_connection()
    cursor = conn.cursor()
    total_deleted = 0

    clean_text = text.replace('!', '').replace(',', '').replace('.', '').replace('?', '').replace('"', '').replace('«', '').replace('»', '')
    words = [word for word in clean_text.split() if len(word) > 2]
    
    if not words:
        words = clean_text.split()
    if not words:
        return 0

    stems = list(set([word[:4].lower() for word in words if len(word) >= 3]))
    if not stems:
        stems = [word.lower() for word in words]

    conditions_reminders = " OR ".join(["text ILIKE %s" for _ in stems])
    params_reminders = [user_id] + [f"%{stem}%" for stem in stems]

    cursor.execute(f"DELETE FROM reminders WHERE user_id = %s AND ({conditions_reminders})", params_reminders)
    total_deleted += cursor.rowcount

    cursor.execute(f"DELETE FROM recurring_tasks WHERE user_id = %s AND ({conditions_reminders})", params_reminders)
    total_deleted += cursor.rowcount

    conditions_events = " OR ".join(["title ILIKE %s" for _ in stems])
    params_events = [user_id] + [f"%{stem}%" for stem in stems]
    
    cursor.execute(f"DELETE FROM events WHERE user_id = %s AND ({conditions_events})", params_events)
    total_deleted += cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()

    return total_deleted

# --- ФУНКЦИИ ЧТЕНИЯ ---
def get_reminders_by_date(user_id, date_str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT text, remind_time FROM reminders WHERE user_id = %s AND remind_time LIKE %s ORDER BY remind_time", (user_id, f"{date_str}%"))
    reminders = cursor.fetchall()
    cursor.close()
    conn.close()
    return reminders

def get_events_by_date(user_id, date_str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM events WHERE user_id = %s AND event_date = %s", (user_id, date_str))
    events = cursor.fetchall()
    cursor.close()
    conn.close()
    return events

def get_schedule_for_period(user_id, start_date_str, end_date_str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT text, remind_time FROM reminders WHERE user_id = %s AND remind_time::date >= %s::date AND remind_time::date <= %s::date ORDER BY remind_time", (user_id, start_date_str, end_date_str))
    reminders = cursor.fetchall()
    
    cursor.execute("SELECT title, event_date FROM events WHERE user_id = %s AND event_date >= %s AND event_date <= %s ORDER BY event_date", (user_id, start_date_str, end_date_str))
    events = cursor.fetchall()
    
    cursor.execute("SELECT text, next_run_time, frequency, interval FROM recurring_tasks WHERE user_id = %s AND next_run_time::date >= %s::date AND next_run_time::date <= %s::date", (user_id, start_date_str, end_date_str))
    recurring = cursor.fetchall()
    
    cursor.close()
    conn.close()
    return reminders, events, recurring

def get_pending_reminders():
    conn = get_db_connection()
    cursor = conn.cursor()
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    cursor.execute("SELECT id, user_id, text FROM reminders WHERE remind_time <= %s AND is_sent = 0", (now_str,))
    reminders = cursor.fetchall()
    cursor.close()
    conn.close()
    return reminders

def get_pending_recurring_tasks():
    conn = get_db_connection()
    cursor = conn.cursor()
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    cursor.execute("SELECT id, user_id, text, next_run_time, frequency, interval FROM recurring_tasks WHERE next_run_time <= %s", (now_str,))
    tasks = cursor.fetchall()
    cursor.close()
    conn.close()
    return tasks

def update_recurring_task_time(task_id, new_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE recurring_tasks SET next_run_time = %s WHERE id = %s", (new_time, task_id))
    conn.commit()
    cursor.close()
    conn.close()

def mark_reminder_as_sent(reminder_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE reminders SET is_sent = 1 WHERE id = %s", (reminder_id,))
    conn.commit()
    cursor.close()
    conn.close()

init_db()

# --- БАНК ЦИТАТ ---
QUOTES = [
    "Работай тяжело, чтобы жить было легко!",
    "Успех — это сумма маленьких усилий, повторяемых день за днем.",
    "Не откладывай на завтра то, что можно сделать сегодня.",
    "Твое будущее создается тем, что ты делаешь сегодня, а не завтра.",
    "Каждое утро — это новый шанс изменить свою жизнь к лучшему.",
    "Дисциплина — это мост между целями и их достижением.",
    "Верь в себя, и все будет возможно.",
    "Лучший способ предсказать будущее — создать его.",
    "Сегодня отличный день, чтобы сделать что-то великое.",
    "Неудача — это возможность начать заново, но уже более мудро. — Генри Форд",
    "Вдохновение приходит только во время работы. — Габриэль Гарсиа Маркес",
    "Постановка целей — это первый шаг к превращению невидимого в видимое. — Тони Роббинс",
    "Лучший способ предсказать ваше будущее — создать его. — Авраам Линкольн",
    "Дайте человеку цель, ради которой стоит жить, и он сможет выжить в любой ситуации. — Иоганн Вольфганг Гёте",
    "Делайте, что можете, с тем, что имеете, там, где вы есть. — Теодор Рузвельт",
    "Каждую ночь, прежде чем уснуть, представляйте себе свой идеальный завтрашний день. Вы увидите, во многом так и будет. — Брайан Трейси"
]

# --- УТРЕННЕЕ СООБЩЕНИЕ ---
def generate_morning_report_text(user_id, first_name):
    today = datetime.now(TZ)
    today_date_str = today.strftime("%Y-%m-%d")
    
    days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    months_ru = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    
    date_str = f"{days_ru[today.weekday()]}, {today.day} {months_ru[today.month-1]} {today.year} года"
    
    report = f"☀️ Доброе утро, {first_name}! Сегодня {date_str}.\n\n"
    
    events = get_events_by_date(user_id, today_date_str)
    if events:
        report += "🎉 События на сегодня:\n"
        for event in events:
            report += f"🎈 {event[0]}\n"
        report += "\n"
    
    quote = random.choice(QUOTES)
    report += f"💡 Цитата дня: {quote}\n\n"
    
    report += "📋 Планы на день:\n"
    reminders = get_reminders_by_date(user_id, today_date_str)
    
    if reminders:
        for text, time_str in reminders:
            short_time = time_str.split(" ")[1]
            report += f"🔔 {short_time} — {text}\n"
    elif not events:
        report += "🔹 На сегодня нет запланированных дел. Отличный день для отдыха или новых идей!\n"
        
    report += "\n🚀 Желаю вам хорошего и продуктивного дня! Все получится!"
    return report

def send_morning_report():
    users = get_all_users()
    for user_id, first_name in users:
        try:
            report_text = generate_morning_report_text(user_id, first_name)
            image_url = get_random_morning_image()
            
            if image_url:
                send_photo_safe(user_id, image_url, report_text)
            else:
                bot.send_message(user_id, report_text, parse_mode='HTML')
                
        except Exception as e:
            print(f"Ошибка отправки утреннего отчета пользователю {user_id}: {e}")

# --- ПРОВЕРКА НАПОМИНАНИЙ ---
def check_reminders():
    pending = get_pending_reminders()
    for rem_id, user_id, text in pending:
        try:
            bot.send_message(user_id, f"🔔 Напоминание:\n{text}")
            mark_reminder_as_sent(rem_id)
        except Exception as e:
            print(f"Ошибка отправки напоминания: {e}")

    recurring_tasks = get_pending_recurring_tasks()
    for task_id, user_id, text, next_time_str, freq, interval in recurring_tasks:
        try:
            bot.send_message(user_id, f"🔄 Регулярная задача:\n{text}")
            
            current_time = datetime.strptime(next_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            
            if freq == "minutes":
                new_time = current_time + timedelta(minutes=interval)
            elif freq == "hours":
                new_time = current_time + timedelta(hours=interval)
            elif freq == "days":
                new_time = current_time + timedelta(days=interval)
            elif freq == "weeks":
                new_time = current_time + timedelta(weeks=interval)
            else:
                new_time = current_time + timedelta(days=1)
                
            new_time_str = new_time.strftime("%Y-%m-%d %H:%M")
            update_recurring_task_time(task_id, new_time_str)
            
        except Exception as e:
            print(f"Ошибка обработки повторяющейся задачи: {e}")

# --- ПЛАНИРОВЩИК ---
scheduler = BackgroundScheduler(timezone=TZ)
scheduler.add_job(send_morning_report, 'cron', hour=7, minute=0)
scheduler.add_job(check_reminders, 'interval', minutes=1)
scheduler.start()

# --- ФЛАСК ДЛЯ RENDER ---
@app.route('/')
def hello_world():
    return "Bot is working!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- ОБРАБОТЧИКИ КОМАНД ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    first_name = message.from_user.first_name or "Друг"
    add_user_to_db(message.chat.id, first_name)
    
    photo_url = "https://i.pinimg.com/736x/98/60/77/986077d1e2e12068dc276d9c027e517b.jpg"
    caption_text = (
        f"<b>Привет, {first_name}!</b>\n"
        "FOCUS на связи. Я — твой помощник в мире больших целей и маленьких шагов.\n\n"
        "Моя задача — чтобы ты не просто мечтал, а действовал. Я запоминаю твои планы, напоминаю о важном и помогаю держать фокус. Всё, что тебе нужно, — сказать мне.\n\n"
        "<b>Начни прямо сейчас:</b>\n"
        "🔔 «Напомни мне завтра в 10:00 позвонить маме»\n"
        "🔄 «Напоминай мне пить воду каждые 2 часа»\n"
        "🎂 «У Друга день рождения 15 ноября»\n"
        "📅 «Какие у меня планы на эту неделю?»\n"
        "❌ «Удали задачу про воду»\n\n"
        "🖼️ <b>Добавь картинку для утреннего отчёта:</b>\n"
        "Отправь мне фото или напиши: «Добавь картинку https://...»\n\n"
        "<i>Твоя цель — мой приоритет. Погнали к результатам! 🏆</i>"
    )

    try:
        send_photo_safe(message.chat.id, photo_url, caption_text)
    except Exception as e:
        bot.send_message(message.chat.id, caption_text, parse_mode='HTML')

@bot.message_handler(commands=['test_morning'])
def test_morning(message):
    first_name = message.from_user.first_name or "Друг"
    user_id = message.chat.id
    report_text = generate_morning_report_text(user_id, first_name)
    image_url = get_random_morning_image()
    
    if image_url:
        send_photo_safe(user_id, image_url, report_text)
    else:
        bot.send_message(user_id, report_text, parse_mode='HTML')

@bot.message_handler(commands=['send_update'])
def broadcast_update(message):
    if MY_CHAT_ID and str(message.chat.id) != MY_CHAT_ID:
        bot.reply_to(message, "⛔ У вас нет прав на эту команду.")
        return

    users = get_all_users()
    update_photo_url = "https://i.pinimg.com/736x/09/33/14/093314fcaa98162712137d40580cf607.jpg"
    update_text = (
        "🚀 <b>ОБНОВЛЕНИЕ!</b>\n\n"
        "Теперь ваш персональный помощник <b>FOCUS</b> может по-новому запоминать задачи. "
        "Ему можно не просто ставить однотипные задачи по типу «напомнить позвонить другу» или «выполнить задачу завтра вечером», "
        "а использовать его для своей утренней или вечерней рутины.\n\n"
        "Напиши ему: «напоминай мне пить воду каждые 2 часа», или «напоминай мне каждое утро отжиматься». "
        "И он будет присылать вам напоминание снова и снова. 🔄\n\n"
        "<i>Работаем для вас!</i>"
    )
    success_count = 0
    for user_id, first_name in users:
        try:
            send_photo_safe(user_id, update_photo_url, update_text)
            success_count += 1
        except Exception:
            try:
                bot.send_message(user_id, update_text, parse_mode='HTML')
                success_count += 1
            except Exception:
                pass
    bot.reply_to(message, f"✅ Рассылка завершена. Сообщение отправлено {success_count} пользователям.")

# --- ОБРАБОТЧИК ВХОДЯЩИХ ИЗОБРАЖЕНИЙ ---
@bot.message_handler(content_types=['photo'])
def handle_photo_upload(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        
        add_morning_image(file_url)
        bot.reply_to(message, "✅ Отлично! Картинка добавлена в банк утренних изображений. (Это самый надежный способ).")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Не удалось сохранить картинку: {str(e)}")

# --- ОБРАБОТЧИК AI С ИНСТРУМЕНТАМИ ---
@bot.message_handler(func=lambda message: True)
def handle_ai_message(message):
    user_text = message.text
    first_name = message.from_user.first_name or "Друг"
    add_user_to_db(message.chat.id, first_name)

    url = "https://api.aiai.by/v1/chat/completions" 
    model_name = "gpt-4o-mini" 

    now = datetime.now(TZ)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    today_date = now.strftime("%Y-%m-%d")
    next_week_date = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_reminder",
                "description": "Добавляет разовую задачу",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Текст напоминания"},
                        "time": {"type": "string", "description": "Время 'YYYY-MM-DD HH:MM'. Если время не указано, ставь '07:00'. Текущее: " + now_str}
                    },
                    "required": ["text", "time"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_recurring_reminder",
                "description": "Добавляет регулярную задачу.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Текст задачи"},
                        "start_time": {"type": "string", "description": "Время первого выполнения 'YYYY-MM-DD HH:MM'. Текущее: " + now_str},
                        "frequency": {"type": "string", "enum": ["minutes", "hours", "days", "weeks"], "description": "Единица измерения"},
                        "interval": {"type": "integer", "description": "Количество единиц"}
                    },
                    "required": ["text", "start_time", "frequency", "interval"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_event",
                "description": "Добавляет событие (день рождения, годовщина)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Название"},
                        "date": {"type": "string", "description": "Дата 'YYYY-MM-DD'. Год: " + str(now.year)}
                    },
                    "required": ["title", "date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_schedule_for_period",
                "description": "Получает расписание на период",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "Начало 'YYYY-MM-DD'. Сегодня: " + today_date},
                        "end_date": {"type": "string", "description": "Конец 'YYYY-MM-DD'"}
                    },
                    "required": ["start_date", "end_date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_task",
                "description": "Удаляет задачу по ключевому слову",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Ключевое слово для поиска и удаления"}
                    },
                    "required": ["text"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_morning_image",
                "description": "Добавляет картинку по URL в банк утренних изображений.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Прямая ссылка на изображение (начинается с http/https)"}
                    },
                    "required": ["url"]
                }
            }
        }
    ]

    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    system_instruction = (
        "Ты - умный, дружелюбный виртуальный помощник. "
        "ПРАВИЛА:\n"
        "1. Разовое напоминание -> 'add_reminder'.\n"
        "2. Регулярная задача -> 'add_recurring_reminder'.\n"
        "3. Событие (дата без времени) -> 'add_event'.\n"
        "4. Вопрос о планах -> 'get_schedule_for_period'.\n"
        "5. Удаление -> 'delete_task'. Передавай ключевое слово в той форме, в которой оно в задаче (например, 'воду', а не 'вода').\n"
        "6. Если пользователь присылает ссылку на картинку и просит добавить её в утренние отчёты -> 'add_morning_image'.\n"
        "7. У тебя нет интернета. Придумывай творческие ответы про погоду/гороскоп."
    )

    data = {
        "model": model_name,
        "messages": [{"role": "system", "content": system_instruction}, {"role": "user", "content": user_text}],
        "tools": tools,
        "tool_choice": "auto"
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        try:
            result = response.json()
        except Exception:
            bot.reply_to(message, f"⚠️ Ошибка API: Сервер вернул некорректный ответ (не JSON). Код: {response.status_code}")
            return

        if not isinstance(result, dict):
            bot.reply_to(message, f"⚠️ Ошибка API: Неверный формат ответа.")
            return

        if 'error' in result:
            error_info = result['error']
            if isinstance(error_info, dict):
                error_msg = error_info.get('message', 'Неизвестная ошибка')
            else:
                error_msg = str(error_info)
            bot.reply_to(message, f"⚠️ Ошибка API: {error_msg}")
            return

        choices = result.get('choices', [])
        if not choices:
            bot.reply_to(message, "⚠️ Ошибка API: Пустой ответ от нейросети.")
            return

        response_message = choices[0].get('message', {})
        if not isinstance(response_message, dict):
            bot.reply_to(message, "⚠️ Ошибка API: Некорректное сообщение.")
            return
        
        tool_calls = response_message.get("tool_calls", [])
        
        if tool_calls and isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict) or "function" not in tool_call:
                    continue
                
                function_info = tool_call["function"]
                if not isinstance(function_info, dict):
                    continue
                    
                function_name = function_info.get("name", "")
                raw_args = function_info.get("arguments", "{}")
                
                if isinstance(raw_args, str):
                    try:
                        arguments = json.loads(raw_args)
                    except Exception:
                        arguments = {}
                elif isinstance(raw_args, dict):
                    arguments = raw_args
                else:
                    arguments = {}
                
                if not isinstance(arguments, dict):
                    arguments = {}

                if function_name == "add_reminder":
                    add_reminder_to_db(message.chat.id, arguments.get("text", ""), arguments.get("time", ""))
                    bot.reply_to(message, f"✅ Я добавил разовую задачу на {arguments.get('time')}:\n{arguments.get('text')}")
                    
                elif function_name == "add_recurring_reminder":
                    freq_ru = {"minutes": "минут", "hours": "часов", "days": "дней", "weeks": "недель"}.get(arguments.get("frequency"), arguments.get("frequency"))
                    add_recurring_to_db(message.chat.id, arguments.get("text", ""), arguments.get("start_time", ""), arguments.get("frequency", ""), arguments.get("interval", 1))
                    bot.reply_to(message, f"🔄 Я добавил регулярную задачу:\n{arguments.get('text')}\n(Каждые {arguments.get('interval')} {freq_ru}. Первый раз: {arguments.get('start_time')})")
                    
                elif function_name == "add_event":
                    add_event_to_db(message.chat.id, arguments.get("title", ""), arguments.get("date", ""))
                    bot.reply_to(message, f"🎉 Я запомнил событие на {arguments.get('date')}:\n{arguments.get('title')}")
                    
                elif function_name == "get_schedule_for_period":
                    start_d = arguments.get("start_date", today_date)
                    end_d = arguments.get("end_date", today_date)
                    reminders, events, recurring = get_schedule_for_period(message.chat.id, start_d, end_d)
                    
                    if not reminders and not events and not recurring:
                        bot.reply_to(message, f"📭 На период с {start_d} по {end_d} у вас пусто.")
                    else:
                        # Красивое форматирование даты
                        if start_d == end_d:
                            try:
                                dt = datetime.strptime(start_d, "%Y-%m-%d")
                                days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
                                months_ru = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
                                date_title = f"на {days_ru[dt.weekday()]} {dt.day} {months_ru[dt.month-1]} {dt.year} года"
                            except Exception:
                                date_title = f"с {start_d} по {end_d}"
                        else:
                            date_title = f"с {start_d} по {end_d}"

                        reply_text = f"📅 Ваше расписание {date_title}:\n\n"
                        
                        if events:
                            reply_text += "🎉 События:\n"
                            for ev in events: 
                                reply_text += f"🎈 {ev[1]} — {ev[0]}\n"
                            reply_text += "\n"
                            
                        if reminders:
                            reply_text += "📋 Разовые задачи:\n"
                            for i, rem in enumerate(reminders, 1):
                                # Берем только время из "YYYY-MM-DD HH:MM"
                                time_part = rem[1].split(" ")[1] if " " in rem[1] else ""
                                time_str = f" ({time_part})" if time_part else ""
                                reply_text += f"{i}) {rem[0]}{time_str}\n"
                            reply_text += "\n"
                            
                        if recurring:
                            reply_text += "🔄 Регулярные задачи:\n"
                            for rec in recurring:
                                text = rec[0]
                                freq = rec[2]
                                interval = rec[3]
                                
                                # Красиво переводим частоту
                                if freq == "days":
                                    if interval == 1:
                                        freq_text = "ежедневно"
                                    elif interval == 2:
                                        freq_text = "через день"
                                    else:
                                        freq_text = f"каждые {interval} дня"
                                elif freq == "hours":
                                    if interval == 1:
                                        freq_text = "каждый час"
                                    else:
                                        freq_text = f"каждые {interval} часа"
                                elif freq == "weeks":
                                    if interval == 1:
                                        freq_text = "еженедельно"
                                    else:
                                        freq_text = f"каждые {interval} недели"
                                elif freq == "minutes":
                                    if interval == 1:
                                        freq_text = "каждую минуту"
                                    else:
                                        freq_text = f"каждые {interval} минут"
                                else:
                                    freq_text = f"каждые {interval} {freq}"
                                
                                reply_text += f"— {text} ({freq_text})\n"
                                
                        bot.reply_to(message, reply_text)
                        
                elif function_name == "delete_task":
                    del_text = arguments.get("text", "")
                    if del_text:
                        deleted_count = delete_all_matching_tasks(message.chat.id, del_text)
                        if deleted_count > 0:
                            bot.reply_to(message, f"🗑️ Успешно удалено ({deleted_count}) задач/событий, содержащих «{del_text}».")
                        else:
                            bot.reply_to(message, f"🤷‍♂️ Я не нашел задач со словом «{del_text}». Возможно, они уже удалены или называются иначе.")
                
                elif function_name == "add_morning_image":
                    img_url = arguments.get("url", "")
                    if img_url:
                        add_morning_image(img_url)
                        bot.reply_to(message, "✅ Картинка добавлена в банк утренних изображений!")
                        
            return
        
        ai_reply = response_message.get("content")
        if not ai_reply:
            ai_reply = "Извините, я не смог обработать запрос."
        
        bot.reply_to(message, ai_reply)
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка кода: {str(e)}")

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.start()
    print("Бот запущен...")
    import time
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            print(f"Ошибка polling: {e}. Перезапуск...")
            time.sleep(15)

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
    
    conn.commit()
    cursor.close()
    conn.close()

# --- ФУНКЦИИ БАЗЫ ДАННЫХ ---
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

# --- ФУНКЦИИ УДАЛЕНИЯ (Ищут везде) ---
def delete_all_matching_tasks(user_id, text):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Ищем совпадения во всех трех таблицах
    cursor.execute("DELETE FROM reminders WHERE user_id = %s AND text ILIKE %s", (user_id, f"%{text}%"))
    cursor.execute("DELETE FROM recurring_tasks WHERE user_id = %s AND text ILIKE %s", (user_id, f"%{text}%"))
    cursor.execute("DELETE FROM events WHERE user_id = %s AND title ILIKE %s", (user_id, f"%{text}%"))
    conn.commit()
    cursor.close()
    conn.close()

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
    "Сегодня отличный день, чтобы сделать что-то великое."
]

# --- УТРЕННЕЕ СООБЩЕНИЕ ---
def generate_morning_report(user_id, first_name):
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
            report = generate_morning_report(user_id, first_name)
            bot.send_message(user_id, report)
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
        "<i>Твоя цель — мой приоритет. Погнали к результатам! 🏆</i>"
    )

    try:
        bot.send_photo(message.chat.id, photo_url, caption=caption_text, parse_mode='HTML')
    except Exception as e:
        bot.send_message(message.chat.id, caption_text, parse_mode='HTML')

@bot.message_handler(commands=['test_morning'])
def test_morning(message):
    first_name = message.from_user.first_name or "Друг"
    bot.reply_to(message, generate_morning_report(message.chat.id, first_name))

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
            bot.send_photo(user_id, update_photo_url, caption=update_text, parse_mode='HTML')
            success_count += 1
        except Exception:
            try:
                bot.send_message(user_id, update_text, parse_mode='HTML')
                success_count += 1
            except Exception:
                pass
    bot.reply_to(message, f"✅ Рассылка завершена. Сообщение отправлено {success_count} пользователям.")

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
                "description": "Добавляет регулярную задачу. ВАЖНО: Если пользователь просит добавить две разные регулярные задачи в одном сообщении, вызывай эту функцию ДВАЖДЫ.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Текст задачи"},
                        "start_time": {"type": "string", "description": "Время первого выполнения 'YYYY-MM-DD HH:MM'. Текущее: " + now_str},
                        "frequency": {"type": "string", "enum": ["minutes", "hours", "days", "weeks"], "description": "Единица измерения"},
                        "interval": {"type": "integer", "description": "Количество единиц (например, 2 для 'каждые 2 часа')"}
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
                "description": "Удаляет задачу, напоминание или событие по ключевому слову. Используй, если пользователь просит удалить или отменить что-то.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Ключевое слово или текст задачи для поиска и удаления (например, 'пить воду', 'Саша', 'отжимания')"}
                    },
                    "required": ["text"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_morning_report",
                "description": "Утренний отчет",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    system_instruction = (
        "Ты - умный, дружелюбный виртуальный помощник. "
        "ПРАВИЛА:\n"
        "1. Разовое напоминание -> 'add_reminder'.\n"
        "2. Регулярная задача -> 'add_recurring_reminder'. Если просят ДВЕ разные регулярные задачи, вызови функцию ДВАЖДЫ.\n"
        "3. Событие (дата без времени) -> 'add_event'.\n"
        "4. Вопрос о планах -> 'get_schedule_for_period'.\n"
        "5. Если просят УДАЛИТЬ задачу (например: 'убери напоминание про воду', 'отмени зарядку'), используй 'delete_task'. Просто передай ключевое слово.\n"
        "6. У тебя нет интернета. Придумывай творческие ответы про погоду/гороскоп."
    )

    data = {
        "model": model_name,
        "messages": [{"role": "system", "content": system_instruction}, {"role": "user", "content": user_text}],
        "tools": tools,
        "tool_choice": "auto"
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        result = response.json()
        
        if 'error' in result:
            bot.reply_to(message, f"⚠️ Ошибка API: {result['error'].get('message')}")
            return
            
        response_message = result['choices'][0]['message']
        
        if response_message.get("tool_calls"):
            for tool_call in response_message["tool_calls"]:
                function_name = tool_call["function"]["name"]
                
                # ЗАЩИТА ОТ ОШИБКИ 'str' object has no attribute 'get'
                raw_args = tool_call["function"].get("arguments", "{}")
                try:
                    arguments = json.loads(raw_args)
                except Exception:
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
                    reminders, events, recurring = get_schedule_for_period(message.chat.id, arguments.get("start_date"), arguments.get("end_date"))
                    
                    if not reminders and not events and not recurring:
                        bot.reply_to(message, f"📭 На период с {arguments.get('start_date')} по {arguments.get('end_date')} у вас пусто.")
                    else:
                        reply_text = f"📅 Ваше расписание с {arguments.get('start_date')} по {arguments.get('end_date')}:\n\n"
                        
                        if events:
                            reply_text += "🎉 События:\n"
                            for ev in events: reply_text += f"🎈 {ev[1]} — {ev[0]}\n"
                            reply_text += "\n"
                            
                        if reminders:
                            reply_text += "📋 Разовые задачи:\n"
                            for rem in reminders: reply_text += f"🔔 {rem[1]} — {rem[0]}\n"
                            reply_text += "\n"
                            
                        if recurring:
                            reply_text += "🔄 Регулярные задачи:\n"
                            for rec in recurring:
                                freq_ru = {"minutes": "мин", "hours": "ч", "days": "дн", "weeks": "нед"}.get(rec[2], rec[2])
                                reply_text += f"🔁 {rec[0]} (каждые {rec[3]} {freq_ru})\n"
                                
                        bot.reply_to(message, reply_text)
                        
                elif function_name == "delete_task":
                    del_text = arguments.get("text", "")
                    if del_text:
                        delete_all_matching_tasks(message.chat.id, del_text)
                        bot.reply_to(message, f"🗑️ Все задачи и события, содержащие «{del_text}», удалены.")
                        
                elif function_name == "get_morning_report":
                    bot.reply_to(message, generate_morning_report(message.chat.id, first_name))
            return
        
        ai_reply = response_message.get("content", "Извините, я не смог обработать запрос.")
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

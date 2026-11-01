import telebot
import requests
import os
import threading
import sqlite3
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

TZ = ZoneInfo('Europe/Minsk')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # 1. Таблица обычных напоминаний
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            remind_time TEXT,
            is_sent INTEGER DEFAULT 0
        )
    ''')
    
    # 2. Таблица повторяющихся задач
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recurring_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            next_run_time TEXT,
            frequency TEXT, 
            interval INTEGER
        )
    ''')
    
    # 3. Таблица событий
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            event_date TEXT
        )
    ''')
    
    # 4. Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def add_user_to_db(user_id, first_name):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name FROM users")
    users = cursor.fetchall()
    conn.close()
    return users

def add_reminder_to_db(user_id, text, remind_time):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO reminders (user_id, text, remind_time) VALUES (?, ?, ?)", (user_id, text, remind_time))
    conn.commit()
    conn.close()

def add_recurring_to_db(user_id, text, next_run_time, frequency, interval):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO recurring_tasks (user_id, text, next_run_time, frequency, interval) VALUES (?, ?, ?, ?, ?)", 
        (user_id, text, next_run_time, frequency, interval)
    )
    conn.commit()
    conn.close()

def add_event_to_db(user_id, title, event_date):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO events (user_id, title, event_date) VALUES (?, ?, ?)", (user_id, title, event_date))
    conn.commit()
    conn.close()

def get_reminders_by_date(user_id, date_str):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT text, remind_time FROM reminders WHERE user_id = ? AND remind_time LIKE ? ORDER BY remind_time", (user_id, f"{date_str}%"))
    reminders = cursor.fetchall()
    conn.close()
    return reminders

def get_events_by_date(user_id, date_str):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM events WHERE user_id = ? AND event_date = ?", (user_id, date_str))
    events = cursor.fetchall()
    conn.close()
    return events

def get_schedule_for_period(user_id, start_date_str, end_date_str):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute("SELECT text, remind_time FROM reminders WHERE user_id = ? AND date(remind_time) >= ? AND date(remind_time) <= ? ORDER BY remind_time", (user_id, start_date_str, end_date_str))
    reminders = cursor.fetchall()
    
    cursor.execute("SELECT title, event_date FROM events WHERE user_id = ? AND event_date >= ? AND event_date <= ? ORDER BY event_date", (user_id, start_date_str, end_date_str))
    events = cursor.fetchall()
    
    conn.close()
    return reminders, events

def get_pending_reminders():
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    cursor.execute("SELECT id, user_id, text FROM reminders WHERE remind_time <= ? AND is_sent = 0", (now_str,))
    reminders = cursor.fetchall()
    conn.close()
    return reminders

def get_pending_recurring_tasks():
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    cursor.execute("SELECT id, user_id, text, next_run_time, frequency, interval FROM recurring_tasks WHERE next_run_time <= ?", (now_str,))
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def update_recurring_task_time(task_id, new_time):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE recurring_tasks SET next_run_time = ? WHERE id = ?", (new_time, task_id))
    conn.commit()
    conn.close()

def mark_reminder_as_sent(reminder_id):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (reminder_id,))
    conn.commit()
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

# --- ПРОВЕРКА ВСЕХ НАПОМИНАНИЙ ---
def check_reminders():
    # 1. Проверяем обычные задачи
    pending = get_pending_reminders()
    for rem_id, user_id, text in pending:
        try:
            bot.send_message(user_id, f"🔔 Напоминание:\n{text}")
            mark_reminder_as_sent(rem_id)
        except Exception as e:
            print(f"Ошибка отправки напоминания: {e}")

    # 2. Проверяем повторяющиеся задачи
    recurring_tasks = get_pending_recurring_tasks()
    for task_id, user_id, text, next_time_str, freq, interval in recurring_tasks:
        try:
            bot.send_message(user_id, f"🔄 Регулярная задача:\n{text}")
            
            current_time = datetime.strptime(next_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            
            if freq == "hours":
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
        "📅 «Какие у меня планы на эту неделю?»\n\n"
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

# ОБНОВЛЕННАЯ КОМАНДА ДЛЯ РАССЫЛКИ С КАРТИНКОЙ
@bot.message_handler(commands=['send_update'])
def broadcast_update(message):
    # Проверка, чтобы рассылку мог делать только владелец
    if MY_CHAT_ID and str(message.chat.id) != MY_CHAT_ID:
        bot.reply_to(message, "⛔ У вас нет прав на эту команду.")
        return

    users = get_all_users()
    
    # Новая картинка для обновления
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
            # Пытаемся отправить картинку с текстом
            bot.send_photo(user_id, update_photo_url, caption=update_text, parse_mode='HTML')
            success_count += 1
        except Exception as e:
            print(f"Не удалось отправить фото обновления пользователю {user_id}: {e}")
            # Если фото не загрузилось, отправляем просто текст
            try:
                bot.send_message(user_id, update_text, parse_mode='HTML')
                success_count += 1
            except Exception as e2:
                print(f"Не удалось отправить текст обновления пользователю {user_id}: {e2}")
            
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
                "description": "Добавляет задачу/напоминание на конкретное время на ОДИН раз (например, 'завтра в 10:00 купить хлеб')",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Текст напоминания"},
                        "time": {
                            "type": "string", 
                            "description": "Время в формате 'YYYY-MM-DD HH:MM'. ВАЖНО: Если пользователь не указал точное время, всегда ставь '07:00'. Текущее время: " + now_str
                        }
                    },
                    "required": ["text", "time"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_recurring_reminder",
                "description": "Добавляет ПОВТОРЯЮЩУЮСЯ задачу (например, 'каждый день', 'каждую пятницу', 'каждые 2 часа')",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Текст задачи (например, 'выпить воду', 'сделать зарядку')"},
                        "start_time": {
                            "type": "string", 
                            "description": "Время ПЕРВОГО выполнения в формате 'YYYY-MM-DD HH:MM'. Если не указано, используй текущее время + интервал. Текущее время: " + now_str
                        },
                        "frequency": {
                            "type": "string", 
                            "enum": ["hours", "days", "weeks"],
                            "description": "Единица измерения частоты: 'hours' (часы), 'days' (дни), 'weeks' (недели)"
                        },
                        "interval": {
                            "type": "integer",
                            "description": "Количество единиц. Например, для 'каждые 2 часа' — frequency='hours', interval=2. Для 'каждый день' — frequency='days', interval=1."
                        }
                    },
                    "required": ["text", "start_time", "frequency", "interval"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_event",
                "description": "Добавляет событие (день рождения, праздник, годовщина). Используй это, когда пользователь называет дату, но не просит напомнить в конкретное время.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Название события"},
                        "date": {
                            "type": "string", 
                            "description": "Дата события в формате 'YYYY-MM-DD'. Текущий год: " + str(now.year)
                        }
                    },
                    "required": ["title", "date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_schedule_for_period",
                "description": "Получает расписание (задачи и события) за период.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "Начало периода 'YYYY-MM-DD'. Сегодня: " + today_date},
                        "end_date": {"type": "string", "description": "Конец периода 'YYYY-MM-DD'. Если 'на неделю': " + next_week_date}
                    },
                    "required": ["start_date", "end_date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_morning_report",
                "description": "Возвращает текст утреннего сообщения",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "Ты - умный, дружелюбный виртуальный помощник. "
        "ПРАВИЛА:\n"
        "1. Если просят напомнить ОДИН РАЗ, используй 'add_reminder'.\n"
        "2. Если просят повторять действие (каждые N часов, каждый день, по пятницам), используй 'add_recurring_reminder'. Если день недели указан ('по пятницам'), вычисли ближайшую дату для первого раза и поставь frequency='weeks', interval=1.\n"
        "3. Если называют дату без времени (ДР, праздник), используй 'add_event'.\n"
        "4. Для вопросов о планах используй 'get_schedule_for_period'.\n"
        "5. У тебя нет интернета. На вопросы о погоде/гороскопе придумывай творческий ответ."
    )

    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_text}
        ],
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
            tool_call = response_message["tool_calls"][0]
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            
            if function_name == "add_reminder":
                rem_text = arguments.get("text")
                rem_time = arguments.get("time")
                add_reminder_to_db(message.chat.id, rem_text, rem_time)
                bot.reply_to(message, f"✅ Хорошо! Я добавил разовую задачу на {rem_time}:\n{rem_text}")
                return
                
            elif function_name == "add_recurring_reminder":
                rem_text = arguments.get("text")
                start_time = arguments.get("start_time")
                freq = arguments.get("frequency")
                interval = arguments.get("interval")
                
                add_recurring_to_db(message.chat.id, rem_text, start_time, freq, interval)
                
                freq_ru = {"hours": "час(ов)", "days": "дн(я/ей)", "weeks": "недел(ь/и)"}.get(freq, freq)
                bot.reply_to(message, f"🔄 Отлично! Я добавил регулярную задачу:\n{rem_text}\n(Каждые {interval} {freq_ru}. Первый раз: {start_time})")
                return
                
            elif function_name == "add_event":
                ev_title = arguments.get("title")
                ev_date = arguments.get("date")
                add_event_to_db(message.chat.id, ev_title, ev_date)
                bot.reply_to(message, f"🎉 Отлично! Я запомнил событие на {ev_date}:\n{ev_title}")
                return
                
            elif function_name == "get_schedule_for_period":
                start_d = arguments.get("start_date")
                end_d = arguments.get("end_date")
                reminders, events = get_schedule_for_period(message.chat.id, start_d, end_d)
                
                if not reminders and not events:
                    bot.reply_to(message, f"📭 На период с {start_d} по {end_d} у вас нет ни задач, ни событий.")
                else:
                    reply_text = f"📅 Ваше расписание с {start_d} по {end_d}:\n\n"
                    if events:
                        reply_text += "🎉 События:\n"
                        for ev in events:
                            reply_text += f"🎈 {ev[1]} — {ev[0]}\n"
                        reply_text += "\n"
                    if reminders:
                        reply_text += "📋 Задачи:\n"
                        for rem in reminders:
                            reply_text += f"🔔 {rem[1]} — {rem[0]}\n"
                    bot.reply_to(message, reply_text)
                return
                
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

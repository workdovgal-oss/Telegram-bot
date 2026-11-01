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
    
    # Таблица напоминаний (задачи на время)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            remind_time TEXT,
            is_sent INTEGER DEFAULT 0
        )
    ''')
    
    # НОВОЕ: Таблица событий (ДР, праздники, годовщины)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            event_date TEXT
        )
    ''')
    
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
    # Получаем задачи и события за период (например, на неделю)
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Задачи
    cursor.execute("SELECT text, remind_time FROM reminders WHERE user_id = ? AND date(remind_time) >= ? AND date(remind_time) <= ? ORDER BY remind_time", (user_id, start_date_str, end_date_str))
    reminders = cursor.fetchall()
    
    # События
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
    
    # События на сегодня (ДР, праздники)
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
    
    bot.reply_to(message, f"Привет, {first_name}! Я твой умный помощник.\n\n"
                          "Я умею запоминать планы, события и напоминать о них. Просто скажи:\n"
                          "«Напомни мне завтра в 10:00 позвонить маме»\n"
                          "«У Саши день рождения 23 декабря»\n"
                          "Или спроси:\n"
                          "«Какие у меня планы на неделю?»")

@bot.message_handler(commands=['test_morning'])
def test_morning(message):
    first_name = message.from_user.first_name or "Друг"
    bot.reply_to(message, generate_morning_report(message.chat.id, first_name))

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
    
    # Вычисляем дату через неделю для запросов "на неделю"
    next_week_date = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_reminder",
                "description": "Добавляет задачу/напоминание на конкретное время (что-то сделать)",
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
                "name": "add_event",
                "description": "Добавляет событие (день рождения, праздник, годовщина). Используй это, когда пользователь называет дату, но не просит напомнить в конкретное время.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Название события (например, 'День рождения Саши')"},
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
                "description": "Получает расписание (и задачи, и события) за определенный период. Используй это для вопросов 'что на неделе', 'планы на завтра' и т.д.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "Начало периода 'YYYY-MM-DD'. Сегодня: " + today_date},
                        "end_date": {"type": "string", "description": "Конец периода 'YYYY-MM-DD'. Если спрашивают 'на неделю', ставь: " + next_week_date}
                    },
                    "required": ["start_date", "end_date"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_morning_report",
                "description": "Возвращает текст утреннего сообщения (план на день, цитата, рутина)",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "Ты - умный, дружелюбный и креативный виртуальный помощник. "
        "ПРАВИЛА:\n"
        "1. Если просят напомнить в конкретное время ('в 10 утра', 'в обед'), используй 'add_reminder'.\n"
        "2. Если говорят о дате без времени ('23 декабря ДР у Саши', '14 февраля праздник'), используй 'add_event'.\n"
        "3. Если спрашивают о планах ('что на сегодня', 'что на неделе', 'есть ли события'), используй 'get_schedule_for_period'.\n"
        "4. У тебя нет интернета. Если спрашивают о гороскопе/погоде, придумай позитивный творческий ответ.\n"
        "5. Обращайся к пользователю по имени, если оно известно."
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
                bot.reply_to(message, f"✅ Хорошо! Я добавил задачу на {rem_time}:\n{rem_text}")
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
                            # Извлекаем дату и время
                            dt_str = rem[1]
                            reply_text += f"🔔 {dt_str} — {rem[0]}\n"
                    
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

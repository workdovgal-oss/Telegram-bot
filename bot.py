import telebot
import requests
import os
import threading
import sqlite3
import json
import random # Добавили для случайных цитат
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            remind_time TEXT,
            is_sent INTEGER DEFAULT 0
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

def get_reminders_by_date(user_id, date_str):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT text, remind_time FROM reminders WHERE user_id = ? AND remind_time LIKE ? ORDER BY remind_time", (user_id, f"{date_str}%"))
    reminders = cursor.fetchall()
    conn.close()
    return reminders

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
    
    # 1. Шапка с датой
    report = f"☀️ Доброе утро, {first_name}! Сегодня {date_str}.\n\n"
    
    # 2. Случайная цитата
    quote = random.choice(QUOTES)
    report += f"💡 Цитата дня: {quote}\n\n"
    
    # 3. Планы на день (только из базы данных)
    report += "📋 Планы на день:\n"
    reminders = get_reminders_by_date(user_id, today_date_str)
    
    if reminders:
        for text, time_str in reminders:
            short_time = time_str.split(" ")[1]
            report += f"🔔 {short_time} — {text}\n"
    else:
        report += "🔹 На сегодня нет запланированных дел. Отличный день для отдыха или новых идей!\n"
        
    # 4. Пожелание
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
                          "Я умею запоминать планы и напоминать о них. Просто скажи:\n"
                          "«Напомни мне завтра в 10:00 позвонить маме»\n"
                          "Или спроси:\n"
                          "«Какие у меня планы на сегодня?»")

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

    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    today_date = datetime.now(TZ).strftime("%Y-%m-%d")
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_reminder",
                "description": "Добавляет напоминание пользователю в базу данных",
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
                "name": "get_reminders",
                "description": "Получает список всех напоминаний пользователя на конкретную дату",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Дата в формате 'YYYY-MM-DD'. Сегодня это: " + today_date}
                    },
                    "required": ["date"]
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
    
    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "Ты - умный виртуальный помощник. Если пользователь просит напомнить, используй 'add_reminder' (если время не указано, ставь 07:00). Если спрашивает о планах, используй 'get_reminders'. Если спрашивает про утреннее сообщение, используй 'get_morning_report'."},
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
                bot.reply_to(message, f"✅ Хорошо! Я добавил напоминание на {rem_time}:\n{rem_text}")
                return
                
            elif function_name == "get_reminders":
                date_str = arguments.get("date")
                reminders = get_reminders_by_date(message.chat.id, date_str)
                
                if not reminders:
                    bot.reply_to(message, f"📭 На {date_str} у вас нет запланированных дел.")
                else:
                    reply_text = f"📋 Ваши планы на {date_str}:\n"
                    for text, time_str in reminders:
                        short_time = time_str.split(" ")[1]
                        reply_text += f"🕒 {short_time} — {text}\n"
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

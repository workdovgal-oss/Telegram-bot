import telebot
import requests
import os
import threading
import sqlite3
import json
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
    conn.commit()
    conn.close()

def add_reminder_to_db(user_id, text, remind_time):
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO reminders (user_id, text, remind_time) VALUES (?, ?, ?)", (user_id, text, remind_time))
    conn.commit()
    conn.close()

def get_reminders_by_date(user_id, date_str):
    # date_str приходит в формате YYYY-MM-DD
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    # Ищем задачи, где дата совпадает
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

# --- УТРЕННЕЕ СООБЩЕНИЕ ---
def generate_morning_report():
    today = datetime.now(TZ)
    days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    months_ru = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    
    date_str = f"{days_ru[today.weekday()]}, {today.day} {months_ru[today.month-1]} {today.year} года"
    
    report = f"☀️ Доброе утро, Алексей! Сегодня {date_str}.\n\n"
    report += "Цитата сегодняшнего дня: Работай тяжело, чтобы жить было легко!\n\n"
    report += "📋 Планы на день:\n"
    report += "- Утренняя рутина (отжимания, книга, завтрак)\n"
    report += "- Сделать документ для Министерства\n"
    report += "- Выезд с Руководством в Колледж на ЕДИ\n"
    report += "- Разобрать рисунки\n\n"
    report += "🎉 Напоминаю, что до вашего юбилея с девушкой осталось 2 недели. 15 ноября будет год. Уже придумали что подарить?"
    
    return report

def send_morning_report():
    if MY_CHAT_ID:
        try:
            bot.send_message(MY_CHAT_ID, generate_morning_report())
        except Exception as e:
            print(f"Ошибка отправки утреннего отчета: {e}")

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
scheduler.add_job(send_morning_report, 'cron', hour=8, minute=0)
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
    bot.reply_to(message, "Привет! Я твой умный помощник.\n\n"
                          "Я умею запоминать планы и напоминать о них. Просто скажи:\n"
                          "«Напомни мне завтра в 10:00 позвонить маме»\n"
                          "Или спроси:\n"
                          "«Какие у меня планы на сегодня?»")

@bot.message_handler(commands=['test_morning'])
def test_morning(message):
    bot.reply_to(message, generate_morning_report())

# --- ОБРАБОТЧИК AI С ИНСТРУМЕНТАМИ ---
@bot.message_handler(func=lambda message: True)
def handle_ai_message(message):
    user_text = message.text
    
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
                        "time": {"type": "string", "description": "Время в формате 'YYYY-MM-DD HH:MM'. Текущее время: " + now_str}
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
        }
    ]

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "Ты - умный виртуальный помощник. Если пользователь просит напомнить о чем-то, используй 'add_reminder'. Если пользователь спрашивает о своих планах на сегодня или завтра, используй 'get_reminders'."},
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
        
        # Если нейросеть решила вызвать инструмент
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
                    # Форматируем время из базы (из '2026-11-01 15:43' делаем просто '15:43')
                    reply_text = f"📋 Ваши планы на {date_str}:\n"
                    for text, time_str in reminders:
                        short_time = time_str.split(" ")[1]
                        reply_text += f"🕒 {short_time} — {text}\n"
                    bot.reply_to(message, reply_text)
                return
        
        # Если инструменты не нужны, просто отвечаем
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

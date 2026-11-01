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

# Устанавливаем часовой пояс (Минск)
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

# --- УТРЕННЕЕ СООБЩЕНИЕ (без изменений) ---
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
                          "Попробуй написать мне обычным текстом:\n"
                          "«Напомни мне сегодня в 15:40 отжаться 30 раз»\n\n"
                          "Команды для теста:\n"
                          "/test_morning - посмотреть утренний отчет\n"
                          "/remind 13:00 почитать книгу - добавить напоминание вручную")

@bot.message_handler(commands=['test_morning'])
def test_morning(message):
    bot.reply_to(message, generate_morning_report())

@bot.message_handler(commands=['remind'])
def add_reminder_command(message):
    try:
        parts = message.text.split(' ', 2)
        if len(parts) < 3:
            bot.reply_to(message, "⚠️ Формат: /remind ЧЧ:ММ текст\nПример: /remind 13:00 почитать книгу")
            return
        
        time_str = parts[1]
        text = parts[2]
        
        now = datetime.now(TZ)
        remind_time = now.replace(hour=int(time_str.split(':')[0]), minute=int(time_str.split(':')[1]), second=0, microsecond=0)
        
        if remind_time < now:
            remind_time += timedelta(days=1)
            
        remind_time_str = remind_time.strftime("%Y-%m-%d %H:%M")
        
        add_reminder_to_db(message.chat.id, text, remind_time_str)
        bot.reply_to(message, f"✅ Принято! Напомню в {time_str}:\n{text}")
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка формата: {str(e)}")

# --- ОБРАБОТЧИК AI С ИСПОЛЬЗОВАНИЕМ ИНСТРУМЕНТОВ (FUNCTION CALLING) ---
@bot.message_handler(func=lambda message: True)
def handle_ai_message(message):
    user_text = message.text
    
    url = "https://api.aiai.by/v1/chat/completions" 
    model_name = "gpt-4o-mini" 

    # Получаем текущее время для нейросети
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    
    # Описываем нейросети, какие инструменты у нее есть
    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_reminder",
                "description": "Добавляет напоминание пользователю в базу данных",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Текст напоминания (что нужно сделать)"
                        },
                        "time": {
                            "type": "string",
                            "description": "Время напоминания в формате 'YYYY-MM-DD HH:MM'. Текущее время: " + now_str
                        }
                    },
                    "required": ["text", "time"]
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
            {"role": "system", "content": "Ты - умный виртуальный помощник. Если пользователь просит напомнить ему о чем-то, обязательно используй инструмент 'add_reminder', чтобы сохранить это. Вычисли правильную дату и время на основе текущего времени, которое передано в описании инструмента."},
            {"role": "user", "content": user_text}
        ],
        "tools": tools,
        "tool_choice": "auto" # Нейросеть сама решает, когда вызывать инструмент
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        result = response.json()
        
        if 'error' in result:
            error_msg = result['error'].get('message', 'Неизвестная ошибка API')
            bot.reply_to(message, f"⚠️ Ошибка API: {error_msg}")
            return
            
        response_message = result['choices'][0]['message']
        
        # Проверяем, решила ли нейросеть вызвать инструмент
        if response_message.get("tool_calls"):
            tool_call = response_message["tool_calls"][0]
            function_name = tool_call["function"]["name"]
            
            if function_name == "add_reminder":
                arguments = json.loads(tool_call["function"]["arguments"])
                rem_text = arguments.get("text")
                rem_time = arguments.get("time")
                
                # Сохраняем в базу данных
                add_reminder_to_db(message.chat.id, rem_text, rem_time)
                
                # Отвечаем пользователю
                bot.reply_to(message, f"✅ Хорошо! Я добавил напоминание на {rem_time}:\n{rem_text}")
                return
        
        # Если инструмент не нужен, просто отвечаем текстом
        ai_reply = response_message.get("content", "Извините, я не смог обработать запрос.")
        bot.reply_to(message, ai_reply)
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка кода: {str(e)}")

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.start()
    print("Бот запущен и слушает сообщения...")
    
    import time
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            print(f"Ошибка polling: {e}. Перезапуск через 15 секунд...")
            time.sleep(15)

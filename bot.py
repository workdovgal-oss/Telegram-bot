import telebot
import requests
import os
import threading
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Используем встроенный модуль Python 3.9+
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
MY_CHAT_ID = os.getenv("MY_CHAT_ID") # Твой ID для утренних сообщений

# Устанавливаем часовой пояс (Минск)
TZ = ZoneInfo('Europe/Minsk')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    # Таблица для напоминаний
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
    # Ищем задачи, время которых наступило, но они еще не отправлены
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
    # В будущем мы будем брать это из базы, а пока хардкод для примера
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

# --- ПРОВЕРКА НАПОМИНАНИЙ (Каждую минуту) ---
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
# Утренний отчет в 08:00
scheduler.add_job(send_morning_report, 'cron', hour=8, minute=0)
# Проверка напоминаний каждую минуту
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
                          "Команды для теста:\n"
                          "/test_morning - посмотреть утренний отчет\n"
                          "/remind 13:00 почитать книгу - добавить напоминание\n"
                          "Или просто напиши мне что-нибудь, и я отвечу с помощью AI!")

@bot.message_handler(commands=['test_morning'])
def test_morning(message):
    bot.reply_to(message, generate_morning_report())

@bot.message_handler(commands=['remind'])
def add_reminder_command(message):
    # Формат: /remind 13:00 почитать книгу
    try:
        parts = message.text.split(' ', 2)
        if len(parts) < 3:
            bot.reply_to(message, "⚠️ Формат: /remind ЧЧ:ММ текст\nПример: /remind 13:00 почитать книгу")
            return
        
        time_str = parts[1]
        text = parts[2]
        
        # Устанавливаем время на сегодня
        now = datetime.now(TZ)
        remind_time = now.replace(hour=int(time_str.split(':')[0]), minute=int(time_str.split(':')[1]), second=0, microsecond=0)
        
        # Если время уже прошло сегодня, ставим на завтра
        if remind_time < now:
            remind_time += timedelta(days=1)
            
        remind_time_str = remind_time.strftime("%Y-%m-%d %H:%M")
        
        add_reminder_to_db(message.chat.id, text, remind_time_str)
        bot.reply_to(message, f"✅ Принято! Напомню в {time_str}:\n{text}")
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка формата: {str(e)}")

# --- ОБРАБОТЧИК AI ---
@bot.message_handler(func=lambda message: True)
def handle_ai_message(message):
    user_text = message.text
    
    url = "https://api.aiai.by/v1/chat/completions" 
    model_name = "gpt-4o-mini" 

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "Ты - полезный виртуальный помощник. Отвечай кратко и по делу."},
            {"role": "user", "content": user_text}
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        result = response.json()
        
        if 'error' in result:
            error_msg = result['error'].get('message', 'Неизвестная ошибка API')
            bot.reply_to(message, f"⚠️ Ошибка API: {error_msg}")
            return
            
        ai_reply = result['choices'][0]['message']['content']
        bot.reply_to(message, ai_reply)
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка кода: {str(e)}")

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.start()
    print("Бот запущен и слушает сообщения...")
    bot.infinity_polling()

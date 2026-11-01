import telebot
import requests
import os
import threading
from flask import Flask

# Получаем токены из переменных окружения Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY") 

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

@app.route('/')
def hello_world():
    return "Bot is working!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой виртуальный помощник на базе AIAI.BY. Чем могу помочь?")

@bot.message_handler(func=lambda message: True)
def handle_ai_message(message):
    user_text = message.text
    
    # --- НАСТРОЙКИ ДЛЯ AIAI.BY ---
    # ВАЖНО: Проверьте точный URL в документации AIAI.BY. 
    # Обычно у OpenAI-совместимых API это /v1/chat/completions
    url = "https://api.aiai.by/v1/chat/completions" 
    
    # Укажите модель, к которой у вас есть доступ на AIAI.BY (например, gpt-4o-mini, deepseek-chat и т.д.)
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

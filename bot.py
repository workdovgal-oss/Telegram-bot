import os
import telebot
import requests
from flask import Flask
from threading import Thread

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask('')

@app.route('/')
def home():
    return "Бот работает!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Бот на связи и готов общаться.")

@bot.message_handler(func=lambda message: True)
def handle_ai_message(message):
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {'Content-Type': 'application/json'}
        data = {
            "contents": [{
                "parts": [{"text": message.text}]
            }]
        }
        
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        
        ai_reply = result['candidates'][0]['content']['parts'][0]['text']
        bot.reply_to(message, ai_reply)
        
    except Exception as e:
        error_text = str(e)
        print(f"Ошибка: {error_text}")
        bot.reply_to(message, f"⚠️ Ошибка: {error_text}")

if __name__ == '__main__':
    t = Thread(target=run_flask)
    t.start()
    
    print("Бот запущен и ждет сообщения...")
    bot.infinity_polling()

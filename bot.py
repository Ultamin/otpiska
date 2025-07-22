import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    CallbackQueryHandler
)
from dotenv import load_dotenv
import os

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
TINKOFF_API_KEY = os.getenv("TINKOFF_API_KEY")
TOKEN = os.getenv("BOT_TOKEN")
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Загрузка базы данных
def load_brokers_db():
    try:
        with open('brokers_database.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.error("Ошибка загрузки базы данных")
        return {"brokers": []}

brokers_db = load_brokers_db()

# Поиск брокеров
def find_brokers(query: str) -> list:
    return [broker for broker in brokers_db["brokers"] 
            if query.lower() in broker["name"].lower()]

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "🔍 Введите название брокера для поиска:\n"
        "Пример: 'Гивмани' или 'Макс Кредит'"
    )

async def search_brokers(update: Update, context: CallbackContext) -> None:
    query = update.message.text
    results = find_brokers(query)
    
    if not results:
        await update.message.reply_text("❌ Брокер не найден. Попробуйте уточнить название.")
        return
    
    if len(results) == 1:
        await show_solution_options(update, results[0])
    else:
        keyboard = [
            [InlineKeyboardButton(broker['name'], callback_data=f"broker_{idx}")]
            for idx, broker in enumerate(results[:10])
        ]
        await update.message.reply_text(
            "🔍 Найдено несколько брокеров:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def show_solution_options(update: Update, broker: dict) -> None:
    keyboard = [
        [InlineKeyboardButton("💰 Платный вариант (149₽)", callback_data=f"paid_{broker['name']}")],
        [InlineKeyboardButton("🆓 Бесплатный вариант", callback_data=f"free_{broker['name']}")]
    ]
    
    await update.message.reply_text(
        f"📌 *{broker['name']}*\n\n"
        "Выберите вариант решения:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_paid_option(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    broker_name = query.data.split('_', 1)[1]
    broker = next(b for b in brokers_db['brokers'] if b['name'] == broker_name)
    
    # Здесь должна быть интеграция с API Тинькофф
    payment_url = f"https://payment.tinkoff.ru/?apiKey={TINKOFF_API_KEY}&amount=14900"
    
    await query.edit_message_text(
        f"✅ Вы выбрали платный вариант для {broker_name}\n\n"
        "Мы полностью автоматизируем процесс отписки:\n"
        "1. Сформируем все необходимые заявления\n"
        "2. Отправим запросы в службу поддержки\n"
        "3. Проконтролируем исполнение\n\n"
        f"Стоимость: 149₽\n"
        f"[Оплатить]({payment_url})\n\n"
        "После оплаты процесс начнется автоматически",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data=f"back_{broker_name}")]
        ])
    )

async def handle_free_option(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    broker_name = query.data.split('_', 1)[1]
    broker = next(b for b in brokers_db['brokers'] if b['name'] == broker_name)
    
    instructions = (
        f"📌 Инструкция для самостоятельной отписки от {broker_name}:\n\n"
        f"1. Отписаться через личный кабинет: {broker['unsubscribe_link'] if broker['unsubscribe_link'] != '–' else 'недоступно'}\n"
        f"2. Написать на email: {broker['email'] if broker['email'] != '–' else 'недоступно'}\n"
        f"3. Позвонить по телефону: {broker['phone'] if broker['phone'] != '–' else 'недоступно'}\n\n"
        "Если сервис не реагирует:\n"
        "• Напишите заявление в банк о блокировке рекуррентных платежей\n"
        "• Подайте жалобу в Роспотребнадзор через Госуслуги"
    )
    
    await query.edit_message_text(
        instructions,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data=f"back_{broker_name}")]
        ])
    )

async def handle_back_button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    broker_name = query.data.split('_', 1)[1]
    broker = next(b for b in brokers_db['brokers'] if b['name'] == broker_name)
    await show_solution_options(update, broker)

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_brokers))
    application.add_handler(CallbackQueryHandler(handle_paid_option, pattern="^paid_"))
    application.add_handler(CallbackQueryHandler(handle_free_option, pattern="^free_"))
    application.add_handler(CallbackQueryHandler(handle_back_button, pattern="^back_"))
    
    application.run_polling()

if __name__ == "__main__":
    main()

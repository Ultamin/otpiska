import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ConversationHandler,
    CallbackQueryHandler
)
from dotenv import load_dotenv
import os

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
SERVICE, CARD, BANK, EMAIL, PHONE = range(5)

# Загрузка базы данных
with open('brokers_database.json', 'r', encoding='utf-8') as f:
    brokers_db = json.load(f)

# Поиск брокера (регистронезависимый)
def find_broker(query):
    results = []
    for broker in brokers_db['brokers']:
        if query.lower() in broker['name'].lower():
            results.append(broker)
    return results

# Обработка команды /start
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🔍 Введите название брокера или часть названия для поиска:\n"
        "Пример: `Гивмани`, `Макс Кредит`",
        parse_mode='Markdown'
    )

# Обработка текстовых сообщений
def handle_search(update: Update, context: CallbackContext):
    query = update.message.text
    results = find_broker(query)
    
    if not results:
        update.message.reply_text("❌ Брокер не найден. Попробуйте уточнить название.")
        return
    
    if len(results) == 1:
        send_broker_info(update, results[0])
    else:
        keyboard = [
            [InlineKeyboardButton(broker['name'], callback_data=f"broker_{idx}")]
            for idx, broker in enumerate(results[:10])  # Ограничение до 10 результатов
        ]
        update.message.reply_text(
            "🔍 Найдено несколько брокеров:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# Отправка информации о брокере
def send_broker_info(update: Update, broker):
    message = (
        f"📌 *{broker['name']}*\n\n"
        f"🔗 Отписаться через ЛК: `{broker['unsubscribe_link'] if broker['unsubscribe_link'] != '–' else 'Нет данных'}`\n"
        f"📧 Email для отказа: `{broker['email'] if broker['email'] != '–' else 'Нет данных'}`\n"
        f"📞 Телефон: `{broker['phone'] if broker['phone'] != '–' else 'Нет данных'}`"
    )
    if isinstance(update, Update):
        update.message.reply_text(message, parse_mode='Markdown')
    else:  # Для CallbackQueryHandler
        update.callback_query.edit_message_text(message, parse_mode='Markdown')

# Обработка нажатий на кнопки
def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    idx = int(query.data.split('_')[1])
    broker = brokers_db['brokers'][idx]
    send_broker_info(update, broker)

async def start(update: Update, context: CallbackContext) -> int:
    """Начало диалога."""
    reply_text = (
        "🔍 Я помогу найти и отменить нежелательные подписки.\n"
        "Для начала укажите:\n"
        "1. Название сервиса (если известно)\n"
        "2. Последние 4 цифры карты\n"
        "3. Банк\n"
        "4. Email или телефон, привязанные к подписке\n\n"
        "Если не знаете сервис - просто напишите 'не знаю'"
    )
    
    keyboard = [
        [InlineKeyboardButton("Не знаю сервис", callback_data="unknown_service")]
    ]
    
    await update.message.reply_text(
        reply_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return SERVICE

async def handle_service(update: Update, context: CallbackContext) -> int:
    """Обработка названия сервиса."""
    user_input = update.message.text.lower()
    context.user_data['service'] = user_input
    
    if user_input in SERVICES:
        service = SERVICES[user_input]
        await update.message.reply_text(
            f"🔹 {service['name']}\n"
            f"Ссылка для отмены: {service['cancel_url']}\n"
            f"Инструкция: {service['instructions']}\n\n"
            "Нужна помощь с чем-то еще?"
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Введите последние 4 цифры карты, с которой идут списания:"
        )
        return CARD

async def handle_card(update: Update, context: CallbackContext) -> int:
    """Обработка номера карты."""
    card = update.message.text
    if not card.isdigit() or len(card) != 4:
        await update.message.reply_text("Пожалуйста, введите ровно 4 последние цифры карты:")
        return CARD
    
    context.user_data['card'] = card
    await update.message.reply_text("Укажите ваш банк:")
    return BANK

async def handle_bank(update: Update, context: CallbackContext) -> int:
    """Обработка названия банка."""
    context.user_data['bank'] = update.message.text
    await update.message.reply_text(
        "Для проверки подписки укажите email или телефон, привязанные к сервису "
        "(если не знаете, напишите 'нет'):"
    )
    return EMAIL

async def handle_email(update: Update, context: CallbackContext) -> int:
    """Финальный шаг с рекомендациями."""
    contact = update.message.text
    context.user_data['contact'] = contact
    
    # Формируем рекомендации
    service = context.user_data.get('service', 'неизвестный сервис')
    
    reply_text = (
        "🔍 На основании ваших данных:\n"
        f"Сервис: {service}\n"
        f"Карта: ****{context.user_data['card']}\n"
        f"Банк: {context.user_data['bank']}\n"
        f"Контакт: {contact}\n\n"
    )
    
    if service == "не знаю" or service not in SERVICES:
        reply_text += (
            "❌ Сервис не распознан. Рекомендую:\n"
            "1. Проверить выписку по карте за 3 месяца - обычно там указан получатель\n"
            "2. Позвонить в банк и заблокировать рекуррентные платежи\n"
            "3. При необходимости оспорить списание через банк\n"
            "4. Если не удается найти - сменить карту"
        )
    else:
        reply_text += (
            "✅ Подписку можно отменить по инструкции выше.\n"
            "Если возникли проблемы, обратитесь в поддержку сервиса."
        )
    
    await update.message.reply_text(reply_text)
    return ConversationHandler.END

async def cancel(update: Update, context: CallbackContext) -> int:
    """Отмена диалога."""
    await update.message.reply_text("Диалог прерван. Если нужно помочь с подписками - нажмите /start")
    return ConversationHandler.END

async def handle_unknown_service(update: Update, context: CallbackContext) -> int:
    """Обработка кнопки 'Не знаю сервис'."""
    query = update.callback_query
    await query.answer()
    context.user_data['service'] = "не знаю"
    await query.edit_message_text("Введите последние 4 цифры карты, с которой идут списания:")
    return CARD

def main() -> None:
    """Запуск бота."""
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SERVICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_service),
                CallbackQueryHandler(handle_unknown_service, pattern="^unknown_service$")
            ],
            CARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_card)],
            BANK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bank)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == "__main__":
    main()

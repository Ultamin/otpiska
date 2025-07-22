import os
import re
import logging
import aiosqlite
import asyncio
import requests
from logging.handlers import RotatingFileHandler
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackContext, CallbackQueryHandler,
    ConversationHandler, PreCheckoutQueryHandler
)
from telegram.error import BadRequest, Conflict
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Проверка переменных окружения
if not all([TOKEN, PAYMENT_TOKEN, DEEPSEEK_API_KEY]):
    raise ValueError("Не найдены обязательные переменные окружения!")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler('unsub_bot.log', maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Конфигурация
DB_PATH = 'unsub_data.db'
GROUP_ID = -1002579257687
PRICE_RUB = 399
PRICE_STARS = 200

# Состояния
FIO, SOURCE, BANK, CARD, EMAIL, PHONE = range(6)

# Паттерны валидации
FIO_PATTERN = re.compile(r'^[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+$')
CARD_PATTERN = re.compile(r'^\d{6}\*\d{4}$')
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHONE_PATTERN = re.compile(r'^\+?\d{10,15}$')

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                fio TEXT, source TEXT, bank TEXT, card TEXT, email TEXT, phone TEXT,
                payment_status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        await db.commit()

async def save_data(user_id, data):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            '''INSERT OR REPLACE INTO users 
            (telegram_id, fio, source, bank, card, email, phone) 
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, data['fio'], data['source'], data['bank'], 
             data['card'], data['email'], data['phone'])
        )
        await db.commit()

async def update_payment_status(user_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET payment_status = ? WHERE telegram_id = ?",
            (status, user_id)
        )
        await db.commit()

async def ask_deepseek(prompt):
    system_prompt = """
    Ты - помощник в боте для отмены подписок. Твоя задача:
    1. Помогать пользователям правильно заполнять форму отписки
    2. Корректировать некорректные вводы данных
    3. Отвечать ТОЛЬКО на вопросы, связанные с отпиской от услуг
    
    Правила:
    - Отвечай кратко (1-2 предложения)
    - Не отвечай на вопросы не по теме
    - Сообщи, если вопрос не относится к отписке
    - Используй формальный и вежливый тон
    - Поправляй ошибки в данных пользователя
    """
    
    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,  # Уменьшаем "креативность"
                "max_tokens": 150,   # Ограничиваем длину ответа
            },
            timeout=10
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"]
        
        # Фильтруем ответы, не связанные с темой
        if "не по теме" in answer.lower() or "не относится" in answer.lower():
            return "Этот вопрос не относится к отписке от услуг. Пожалуйста, задавайте вопросы только по теме отмены подписок."
        return answer
        
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        return "Произошла ошибка. Пожалуйста, продолжайте заполнение формы."

async def handle_free_text(update: Update, context: CallbackContext):
    user_message = update.message.text
    user_id = update.message.from_user.id
    
    if context.user_data.get('contact_admin'):
        await context.bot.send_message(GROUP_ID, f"✉️ Сообщение от пользователя {user_id}:\n{user_message}")
        await update.message.reply_text("✅ Ваше сообщение отправлено администратору.")
        context.user_data['contact_admin'] = False
        return
    
    # Если пользователь в процессе заполнения формы, перенаправляем его
    current_state = await context.application.persistence.get_conversation(update.effective_chat.id)
    if current_state in [FIO, SOURCE, BANK, CARD, EMAIL, PHONE]:
        await update.message.reply_text("Пожалуйста, продолжайте заполнение формы.")
        return
    
    # Обработка только тематических вопросов
    response = await ask_deepseek(user_message)
    await update.message.reply_text(response)

async def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("✅ Продолжить", callback_data="start_form")],
        [InlineKeyboardButton("📨 Связаться с админом", callback_data="contact_admin")],
        [InlineKeyboardButton("ℹ️ О сервисе", callback_data="about_service")]
    ]
    await update.message.reply_text(
        "👋 <b>Здравствуйте!</b>\nВы обратились в <i>Отписка Бот</i> для отмены нежелательных списаний.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def handle_buttons(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    if query.data == "start_form":
        context.user_data.clear()
        context.user_data['state'] = FIO
        await query.edit_message_text("👤 Введите ФИО полностью (Фамилия Имя Отчество):")
        return FIO
    elif query.data == "contact_admin":
        await query.edit_message_text("📨 Напишите ваше сообщение для админа:")
        context.user_data["contact_admin"] = True
        return ConversationHandler.END
    elif query.data == "about_service":
        await query.edit_message_text(
            "ℹ️ <b>О сервисе:</b>\n\nМы помогаем отменить нежелательные подписки.\nСрок: 1-3 рабочих дня.\nСтоимость: 399 руб.",
            parse_mode="HTML"
        )
        return ConversationHandler.END
    return ConversationHandler.END

async def process_fio(update: Update, context: CallbackContext):
    text = update.message.text
    if not FIO_PATTERN.match(text):
        response = await ask_deepseek(f"Пользователь ввел '{text}'. Попроси исправить на 'Фамилия Имя Отчество'.", "ФИО")
        if response:
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ Пожалуйста, введите ФИО в формате 'Фамилия Имя Отчество' (например: Иванов Иван Иванович).")
        return FIO
    
    context.user_data["fio"] = text
    context.user_data['state'] = SOURCE
    await update.message.reply_text("📃 Укажите название сервиса, от которого хотите отписаться (например: Бигзайм, Prolads и т.д.):")
    return SOURCE

async def process_source(update: Update, context: CallbackContext):
    context.user_data["source"] = update.message.text
    context.user_data['state'] = BANK
    await update.message.reply_text("🏦 Введите название банка, с карты которого происходят списания (например: Тинькофф, Сбербанк):")
    return BANK

async def process_bank(update: Update, context: CallbackContext):
    context.user_data["bank"] = update.message.text
    context.user_data['state'] = CARD
    await update.message.reply_text("💳 Введите первые 6 и последние 4 цифры карты в формате 123456*7890:")
    return CARD

async def process_card(update: Update, context: CallbackContext):
    text = update.message.text
    if not CARD_PATTERN.match(text):
        response = await ask_deepseek(f"Пользователь ввел '{text}'. Попроси исправить на формат '123456*7890'.", "номер карты")
        if response:
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ Пожалуйста, введите номер карты в формате '123456*7890' (первые 6 и последние 4 цифры).")
        return CARD
    
    context.user_data["card"] = text
    context.user_data['state'] = EMAIL
    await update.message.reply_text("📧 Введите email, привязанный к подписке:")
    return EMAIL

async def process_email(update: Update, context: CallbackContext):
    text = update.message.text
    if not EMAIL_PATTERN.match(text):
        response = await ask_deepseek(f"Пользователь ввел '{text}'. Попроси исправить на валидный email.", "email")
        if response:
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ Пожалуйста, введите корректный email (например: example@mail.ru).")
        return EMAIL
    
    context.user_data["email"] = text
    context.user_data['state'] = PHONE
    await update.message.reply_text("📱 Введите номер телефона, привязанный к подписке (10-15 цифр, можно с +7 или 8):")
    return PHONE

async def process_phone(update: Update, context: CallbackContext):
    text = update.message.text
    if not PHONE_PATTERN.match(text):
        response = await ask_deepseek(f"Пользователь ввел '{text}'. Попроси исправить на 10-15 цифр.", "номер телефона")
        if response:
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ Пожалуйста, введите номер телефона (10-15 цифр, можно с +7 или 8).")
        return PHONE
    
    context.user_data["phone"] = text
    user_id = update.message.from_user.id
    
    await save_data(user_id, context.user_data)
    
    await context.bot.send_message(
        GROUP_ID,
        f"<b>Новая заявка:</b>\n"
        f"👤 {context.user_data['fio']}\n"
        f"📄 Сервис: {context.user_data['source']}\n"
        f"🏦 Банк: {context.user_data['bank']}\n"
        f"💳 Карта: {context.user_data['card']}\n"
        f"📧 Email: {context.user_data['email']}\n"
        f"📱 Телефон: {context.user_data['phone']}",
        parse_mode="HTML"
    )
    
    keyboard = [
        [InlineKeyboardButton(f"💳 Оплатить {PRICE_RUB}₽", callback_data=f"pay_rub:{PRICE_RUB}")],
        [InlineKeyboardButton(f"⭐ Оплатить {PRICE_STARS}⭐", callback_data=f"pay_stars:{PRICE_STARS}")]
    ]
    await update.message.reply_text(
        "✅ <b>Спасибо за заявку!</b>\n\n"
        "💰 Для завершения оформления оплатите услугу:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    
    context.user_data.clear()
    return ConversationHandler.END


async def handle_free_text(update: Update, context: CallbackContext):
    user_message = update.message.text
    user_id = update.message.from_user.id
    
    if context.user_data.get('contact_admin'):
        await context.bot.send_message(GROUP_ID, f"✉️ Сообщение от пользователя {user_id}:\n{user_message}")
        await update.message.reply_text("✅ Ваше сообщение отправлено администратору.")
        context.user_data['contact_admin'] = False
        return
    
    try:
        response = await ask_deepseek(user_message)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки вопроса: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при обработке вашего вопроса. Попробуйте позже.")

async def handle_payment_button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data.startswith("pay_rub:"):
            amount = int(query.data.split(":")[1])
            await send_invoice(context.bot, query.from_user.id, amount, "RUB", "Отписка (рубли)")
        elif query.data.startswith("pay_stars:"):
            amount = int(query.data.split(":")[1])
            await send_invoice(context.bot, query.from_user.id, amount, "STAR", "Отписка (звёзды)")
    except BadRequest as e:
        logger.error(f"Payment error: {e}")
        await query.edit_message_text("⚠️ Ошибка платежа. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await query.edit_message_text("⚠️ Произошла ошибка. Попробуйте позже.")
    else:
        await query.edit_message_reply_markup(reply_markup=None)

async def send_invoice(bot, chat_id, amount, currency, title):
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description="Отмена нежелательных подписок",
        payload="unsubscription_service",
        provider_token=PAYMENT_TOKEN,
        currency=currency,
        prices=[LabeledPrice("Услуга", amount * 100)]
    )

async def pre_checkout(update: Update, context: CallbackContext):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    payment = update.message.successful_payment
    
    await update_payment_status(user_id, "completed")
    
    await context.bot.send_message(
        GROUP_ID,
        f"✅ Оплата: {update.message.from_user.full_name} (ID {user_id})\n"
        f"Сумма: {payment.total_amount / 100} {payment.currency}"
    )
    
    await update.message.reply_text(
        "✅ Платёж подтверждён! Мы начали работу.\n"
        "Срок: 1-3 рабочих дня."
    )

async def cancel(update: Update, context: CallbackContext):
    await update.message.reply_text("❌ Процесс отменён.")
    context.user_data.clear()
    return ConversationHandler.END

async def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Update {update} caused error: {context.error}")
    if update.callback_query:
        await update.callback_query.answer("⚠️ Ошибка. Попробуйте снова.")
    elif update.message:
        await update.message.reply_text("⚠️ Ошибка. Попробуйте снова.")

def main():
    # Создаем новый event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Инициализация БД
        loop.run_until_complete(init_db())
        
        # Создание и настройка приложения
        application = Application.builder().token(TOKEN).build()
        
        # Удаление вебхука перед запуском
        loop.run_until_complete(application.bot.delete_webhook(drop_pending_updates=True))
        
        # Настройка ConversationHandler
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_fio)],
                SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_source)],
                BANK: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_bank)],
                CARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_card)],
                EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_email)],
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_phone)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
        
        # Добавление обработчиков
        application.add_handler(conv_handler)
        application.add_handler(CallbackQueryHandler(handle_buttons, pattern="^(start_form|contact_admin|about_service)$"))
        application.add_handler(CallbackQueryHandler(handle_payment_button, pattern="^pay_(rub|stars):"))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
        application.add_handler(PreCheckoutQueryHandler(pre_checkout))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
        application.add_error_handler(error_handler)
        
        # Запуск бота
        logger.info("Бот запущен")
        application.run_polling()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
    finally:
        loop.close()

if __name__ == "__main__":
    main()

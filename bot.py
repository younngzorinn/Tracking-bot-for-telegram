import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Update
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware

# Конфигурация
API_TOKEN = os.getenv('API_TOKEN')
ADMIN_CHAT_ID = 579542680  # Ваш chat_id
ALLOWED_USERS = [ADMIN_CHAT_ID]

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ===== ИСПРАВЛЕННЫЙ Middleware =====
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Update, data):
        # Извлекаем пользователя в зависимости от типа события
        user_id = None
        
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
        elif event.edited_message:
            user_id = event.edited_message.from_user.id
        elif event.channel_post:
            user_id = event.channel_post.from_user.id if event.channel_post.from_user else None
        elif event.edited_channel_post:
            user_id = event.edited_channel_post.from_user.id if event.edited_channel_post.from_user else None
        
        # Если не удалось извлечь user_id или он не в списке разрешенных
        if not user_id or user_id not in ALLOWED_USERS:
            # Пытаемся ответить в зависимости от типа события
            if event.callback_query:
                await event.callback_query.answer("⛔ Доступ запрещен!", show_alert=True)
            elif event.message:
                await event.message.answer("⛔ Доступ запрещен!")
            return False
        
        return await handler(event, data)

# Регистрируем middleware
dp.update.outer_middleware(AccessMiddleware())

# ===== КОМАНДЫ =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Новости ETH", callback_data="news_example")],
        [InlineKeyboardButton(text="График ETH", callback_data="chart_example")],
        [InlineKeyboardButton(text="Ликвидации", callback_data="liquidation_example")],
        [InlineKeyboardButton(text="Whale Alert", callback_data="whale_example")]
    ])
    await message.answer(
        "🚀 <b>Ethereum Tracker Bot</b>\n\n"
        "Выберите пример уведомления:",
        reply_markup=kb
    )

# ===== ПРИМЕРЫ УВЕДОМЛЕНИЙ =====
# ... остальной код без изменений (как в предыдущих примерах) ...

# ===== ЗАПУСК БОТА =====
async def main():
    logging.basicConfig(level=logging.INFO)
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        # Закрытие сессий
        await bot.session.close()
        if hasattr(bot, '_session'):
            await bot._session.close()
        if hasattr(bot, '_client_session') and not bot._client_session.closed:
            await bot._client_session.close()

if __name__ == "__main__":
    asyncio.run(main())

import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties  # Важный импорт!

# Конфигурация
API_TOKEN = os.getenv('API_TOKEN')  # Получаем токен из переменных окружения
ADMIN_CHAT_ID = 123456789  # Ваш chat_id для тестов
ALLOWED_USERS = [ADMIN_CHAT_ID]  # Список разрешенных пользователей

# Инициализация бота с исправлением
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ===== Middleware для приватного доступа =====
async def check_user_access(handler, event, data):
    if event.from_user.id not in ALLOWED_USERS:
        await event.answer("⛔ Доступ запрещен!")
        return False
    return await handler(event, data)

dp.update.middleware(check_user_access)

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
@dp.callback_query(lambda c: c.data == "news_example")
async def news_example(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "❗️❗️❗️ <b>СРОЧНЫЕ НОВОСТИ ETH</b> ❗️❗️❗️\n\n"
        "🔥 Vitalik Buterin предложил масштабное обновление сети\n"
        "📍 Источник: CoinDesk\n"
        "<a href='https://example.com'>Читать полностью</a>",
        disable_web_page_preview=True
    )

@dp.callback_query(lambda c: c.data == "chart_example")
async def chart_example(callback: types.CallbackQuery):
    # Для фото нужно явно указать parse_mode=None
    await callback.message.answer_photo(
        photo="https://s3.coinmarketcap.com/generated/sparklines/web/7d/2781/1027.svg",
        caption="📊 <b>Анализ 4H свечи ETH/USDT</b>\n\n"
                "▫️ <b>Текущая цена:</b> $3785.42 (+2.3%)\n"
                "▫️ <b>Ключевые уровни:</b>\n"
                "Поддержка: $3750 | $3680\n"
                "Сопротивление: $3820 | $3900\n\n"
                "🟢 Сценарий: Пробитие $3820 может открыть путь к $4000",
        parse_mode=None  # Явное указание для фото
    )

@dp.callback_query(lambda c: c.data == "liquidation_example")
async def liquidation_example(callback: types.CallbackQuery):
    await callback.message.answer(
        "📉 <b>КРУПНАЯ ЛИКВИДАЦИЯ ETH!</b>\n\n"
        "▫️ Биржа: <b>Binance</b>\n"
        "▫️ Направление: <b>LONG</b> ▫️ Сумма: <b>$2.1M</b>\n"
        "▫️ Цена: $3776.40\n"
        "▫️ Время: 12:45 UTC\n\n"
        "#ETH #Liquidation"
    )

@dp.callback_query(lambda c: c.data == "whale_example")
async def whale_example(callback: types.CallbackQuery):
    await callback.message.answer(
        "🐋 <b>WHALE ALERT!</b> 🚨\n\n"
        "▫️ Сумма: <b>24,500 ETH</b> ($92.4M)\n"
        "▫️ От: Binance\n"
        "▫️ К: неизвестный кошелек\n"
        "▫️ Транзакция: <a href='https://etherscan.io/tx/0x...'>Etherscan</a>\n\n"
        "📍 Классификация: КИТОВАЯ ТРАНЗАКЦИЯ"
    )

# ===== ЗАПУСК БОТА =====
async def main():
    logging.basicConfig(level=logging.INFO)
    try:
        await dp.start_polling(bot, skip_updates=True)  # Пропустить накопившиеся апдейты
    except Exception as e:
        logging.error(f"Fatal error: {e}")
    finally:
        try:
            # Закрытие всех сессий
            await bot.session.close()
            if hasattr(bot, '_session'):
                await bot._session.close()
            # Дополнительное закрытие для aiohttp
            if hasattr(bot, '_client_session') and not bot._client_session.closed:
                await bot._client_session.close()
        except Exception as e:
            logging.error(f"Session close error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
        # Дополнительные действия при остановке

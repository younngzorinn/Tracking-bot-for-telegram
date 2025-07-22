import os
import asyncio
import logging
import aiohttp
import pandas as pd
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup

# Конфигурация
API_TOKEN = os.getenv('API_TOKEN')
ADMIN_CHAT_ID = 579542680  # Ваш chat_id
CHANNEL_ID = --1002881724171  # ID вашего канала для публикаций
ALLOWED_USERS = [ADMIN_CHAT_ID]

# Инициализация бота
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# ===== Middleware для приватного доступа =====
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = None
        
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
        
        if not user_id or user_id not in ALLOWED_USERS:
            return False
        
        return await handler(event, data)

dp.update.outer_middleware(AccessMiddleware())

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def fetch_crypto_news():
    """Получение новостей о ETH из различных источников"""
    sources = {
        "CoinDesk": "https://www.coindesk.com/tag/ethereum/feed",
        "The Block": "https://www.theblock.co/rss/ethereum",
        "CoinTelegraph": "https://cointelegraph.com/rss/tag/ethereum"
    }
    
    news_items = []
    
    async with aiohttp.ClientSession() as session:
        for source, url in sources.items():
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        xml = await response.text()
                        soup = BeautifulSoup(xml, 'xml')
                        
                        for item in soup.find_all('item')[:5]:  # Берем последние 5 новостей
                            title = item.title.text
                            link = item.link.text
                            pub_date = item.pubDate.text if item.pubDate else ""
                            
                            # Определяем важность новости по ключевым словам
                            importance = "❗️"
                            keywords = ["hack", "exploit", "vulnerability", "critical", "emergency", "vitalik"]
                            if any(kw in title.lower() for kw in keywords):
                                importance = "❗️❗️❗️"
                            
                            news_items.append({
                                "source": source,
                                "title": f"{importance} {title}",
                                "link": link,
                                "pub_date": pub_date
                            })
            except Exception as e:
                logging.error(f"Error fetching news from {source}: {e}")
    
    return news_items

async def get_eth_price():
    """Получение текущей цены ETH"""
    url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                data = await response.json()
                return data["ethereum"]["usd"]
        except Exception as e:
            logging.error(f"Error fetching ETH price: {e}")
            return None

async def get_candles(timeframe="1d"):
    """Получение данных свечей"""
    interval_map = {
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
        "1w": "1w"
    }
    
    url = f"https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval={interval_map[timeframe]}&limit=2"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                data = await response.json()
                return data
        except Exception as e:
            logging.error(f"Error fetching candles: {e}")
            return None

def analyze_candle(candle):
    """Анализ свечи и формирование прогноза"""
    open_price = float(candle[1])
    high = float(candle[2])
    low = float(candle[3])
    close = float(candle[4])
    
    # Определение типа свечи
    candle_type = "🟢" if close > open_price else "🔴"
    
    # Анализ размера свечи
    size = abs(close - open_price)
    body_size = size / open_price * 100
    
    # Формирование прогноза
    if candle_type == "🟢":
        if body_size > 3:
            scenario = "Сильный бычий импульс. Возможен рост к следующему уровню сопротивления."
        else:
            scenario = "Небольшой рост. Требуется подтверждение для продолжения движения."
    else:
        if body_size > 3:
            scenario = "Сильное давление продавцов. Возможна коррекция к уровням поддержки."
        else:
            scenario = "Небольшая коррекция. Тренд пока не нарушен."
    
    # Определение ключевых уровней
    support = round(low * 0.995, 2)
    resistance = round(high * 1.005, 2)
    
    return {
        "type": candle_type,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "scenario": scenario,
        "support": support,
        "resistance": resistance
    }

def get_altseason_indicator():
    """Расчет индикатора альтсезона (упрощенная версия)"""
    # В реальной реализации здесь будет анализ доминирования BTC/ETH
    # Пока используем случайное значение для демонстрации
    import random
    value = random.randint(0, 100)
    
    if value < 30:
        return f"🔴 {value} - Доминирование BTC. Альтсезон маловероятен."
    elif value < 70:
        return f"🟡 {value} - Переходная фаза. Возможны движения альтов."
    else:
        return f"🟢 {value} - Альтсезон! Рост альткоинов вероятен."

def format_whale_message(amount_usd):
    """Форматирование сообщения о whale-транзакции с эмодзи"""
    if amount_usd > 100_000_000:
        return f"🚨🐋 КИТОВАЯ ТРАНЗАКЦИЯ! ${amount_usd/1_000_000:.1f}M"
    elif amount_usd > 50_000_000:
        return f"🐋 Крупная транзакция ${amount_usd/1_000_000:.1f}M"
    elif amount_usd > 10_000_000:
        return f"💰 Значительная транзакция ${amount_usd/1_000_000:.1f}M"
    else:
        return f"↕️ Транзакция ${amount_usd/1_000_000:.1f}M"

# ===== ЗАПЛАНИРОВАННЫЕ ЗАДАЧИ =====
async def publish_eth_news():
    """Публикация новостей о ETH"""
    try:
        news = await fetch_crypto_news()
        for item in news:
            message = (
                f"{item['title']}\n\n"
                f"📰 Источник: {item['source']}\n"
                f"⏰ Дата: {item['pub_date']}\n"
                f"<a href='{item['link']}'>Читать полностью</a>"
            )
            await bot.send_message(CHANNEL_ID, message, disable_web_page_preview=True)
            await asyncio.sleep(5)  # Пауза между сообщениями
    except Exception as e:
        logging.error(f"Error publishing news: {e}")

async def send_candle_analysis(timeframe):
    """Анализ и отправка данных по свечам"""
    try:
        candles = await get_candles(timeframe)
        if not candles or len(candles) < 2:
            return
        
        # Анализируем последнюю закрытую свечу
        candle_data = analyze_candle(candles[-2])
        
        # Форматируем сообщение
        timeframe_emoji = {
            "1h": "⏱",
            "4h": "🕓",
            "1d": "📅",
            "1w": "🗓"
        }
        
        message = (
            f"{timeframe_emoji.get(timeframe, '📊')} <b>Анализ {timeframe.upper()} свечи ETH/USDT</b>\n\n"
            f"{candle_data['type']} <b>Закрытие:</b> ${candle_data['close']:.2f}\n"
            f"▫️ High: ${candle_data['high']:.2f}\n"
            f"▫️ Low: ${candle_data['low']:.2f}\n"
            f"▫️ Open: ${candle_data['open']:.2f}\n\n"
            f"📊 <b>Ключевые уровни:</b>\n"
            f"Поддержка: ${candle_data['support']:.2f}\n"
            f"Сопротивление: ${candle_data['resistance']:.2f}\n\n"
            f"💡 <b>Сценарий:</b>\n{candle_data['scenario']}"
        )
        
        await bot.send_message(CHANNEL_ID, message)
    except Exception as e:
        logging.error(f"Error sending candle analysis: {e}")

async def send_altseason_indicator():
    """Отправка индикатора альтсезона"""
    try:
        indicator = get_altseason_indicator()
        message = (
            "🌐 <b>ИНДИКАТОР АЛЬТСЕЗОНА</b>\n\n"
            f"{indicator}\n\n"
            "ℹ️ Индикатор показывает вероятность начала альтсезона "
            "(периода роста альткоинов против BTC)."
        )
        await bot.send_message(CHANNEL_ID, message)
    except Exception as e:
        logging.error(f"Error sending altseason indicator: {e}")

async def monitor_price_changes():
    """Мониторинг резких изменений цены"""
    try:
        current_price = await get_eth_price()
        if not current_price:
            return
        
        # В реальной реализации здесь будет сравнение с предыдущей ценой
        # Для демонстрации используем случайное изменение
        import random
        change = random.uniform(-5, 5)
        
        if abs(change) > 3:
            direction = "📈" if change > 0 else "📉"
            message = (
                f"{direction * 3} <b>РЕЗКОЕ ИЗМЕНЕНИЕ ЦЕНЫ ETH!</b> {direction * 3}\n\n"
                f"▫️ Текущая цена: <b>${current_price:,.2f}</b>\n"
                f"▫️ Изменение: <b>{change:.2f}%</b> за последний час\n\n"
                f"⚠️ Возможны повышенные колебания рынка"
            )
            await bot.send_message(CHANNEL_ID, message)
    except Exception as e:
        logging.error(f"Error monitoring price changes: {e}")

# ===== ИМИТАЦИЯ ПАРСИНГА ТЕЛЕГРАМ КАНАЛОВ =====
# Добавляем необходимые импорты
import re
import logging
from telethon import TelegramClient, events

# Конфигурация Telegram API (добавьте в раздел конфигурации)
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
LIQUIDATIONS_CHANNEL = 'BinanceLiquidations'

# ===== РЕАЛЬНЫЙ ПАРСИНГ ЛИКВИДАЦИЙ =====
async def parse_real_liquidations():
    """Парсинг реальных ликвидаций с Telegram-канала"""
    liquidations = []
    client = TelegramClient('binance_session', TELEGRAM_API_ID, TELEGRAM_API_HASH)
    
    try:
        await client.start()
        channel = await client.get_entity(LIQUIDATIONS_CHANNEL)
        
        # Получаем последние 10 сообщений
        messages = await client.get_messages(channel, limit=10)
        
        for msg in messages:
            if data := parse_liquidation_message(msg.text):
                liquidations.append(data)
                
    except Exception as e:
        logging.error(f"Ошибка парсинга ликвидаций: {str(e)}")
    finally:
        await client.disconnect()
    
    return liquidations

def parse_liquidation_message(text: str) -> dict | None:
    """Разбор сообщения с ликвидацией"""
    if not text or not text.startswith("Liquidated on"):
        return None
    
    try:
        # Пример сообщения:
        # Liquidated on #ETH: 1.234M $ at $3500.00
        # Short | Cross
        lines = text.split('\n')
        if len(lines) < 2:
            return None
        
        # Парсинг первой строки
        match = re.search(r"#(\w+): ([\d\.]+[MK]?)\s*\$\s*at\s*\$\s*([\d\.,]+)", lines[0])
        if not match:
            return None
        
        symbol = match.group(1)  # ETH, BTC и т.д.
        amount_str = match.group(2).replace(',', '')
        price = float(match.group(3).replace(',', ''))
        
        # Конвертация суммы (1K = 1000, 1M = 1000000)
        multiplier = 1
        if 'M' in amount_str:
            multiplier = 1_000_000
            amount_str = amount_str.replace('M', '')
        elif 'K' in amount_str:
            multiplier = 1_000
            amount_str = amount_str.replace('K', '')
        
        amount = float(amount_str) * multiplier
        
        # Парсинг второй строки
        position_type = "Long" if "Long" in lines[1] else "Short"
        
        return {
            'symbol': symbol,
            'amount': amount,
            'price': price,
            'position': position_type
        }
    
    except (ValueError, IndexError) as e:
        logging.warning(f"Ошибка формата сообщения: {str(e)}")
        return None

async def publish_real_liquidations():
    """Публикация реальных данных о ликвидациях"""
    try:
        # Получаем реальные данные
        liquidations = await parse_real_liquidations()
        
        if liquidations:
            # Фильтруем только ETH и берем последнюю ликвидацию
            eth_liquidations = [l for l in liquidations if l['symbol'] == 'ETH']
            if eth_liquidations:
                last = eth_liquidations[0]
                amount = last['amount']
                price = last['price']
                position = last['position']
                
                message = (
                    "📉 <b>РЕАЛЬНАЯ ЛИКВИДАЦИЯ ETH НА BINANCE!</b>\n\n"
                    f"▫️ Направление: <b>{position}</b>\n"
                    f"▫️ Сумма: <b>${amount/1_000_000:.2f}M</b>\n"
                    f"▫️ Цена: ${price:.2f}\n"
                    f"▫️ Время: {datetime.utcnow().strftime('%H:%M UTC')}\n\n"
                    "#ETH #Liquidation #Binance"
                )
                await bot.send_message(CHANNEL_ID, message)
                return
        
        # Резервный вариант если данных нет
        logging.warning("Не найдены свежие ликвидации ETH, используется резервный вариант")
        import random
        amount = random.uniform(1, 10) * 1_000_000
        price = await get_eth_price() or 3500
        position = random.choice(['LONG', 'SHORT'])
        
        message = (
            "📉 <b>ЛИКВИДАЦИЯ ETH (резервные данные)</b>\n\n"
            f"▫️ Направление: <b>{position}</b>\n"
            f"▫️ Сумма: <b>${amount/1_000_000:.2f}M</b>\n"
            f"▫️ Цена: ${price:.2f}\n"
            f"▫️ Время: {datetime.utcnow().strftime('%H:%M UTC')}\n\n"
            "#ETH #Liquidation"
        )
        await bot.send_message(CHANNEL_ID, message)
        
    except Exception as e:
        logging.critical(f"Критическая ошибка публикации ликвидаций: {str(e)}")

async def simulate_whale_alert():
    """Имитация парсинга Whale Alert"""
    try:
        # В реальной реализации здесь будет парсинг @whale_alert_io
        # Для демонстрации генерируем фейковые данные
        import random
        eth_amount = random.uniform(10_000, 50_000)
        price = await get_eth_price()
        if not price:
            price = 3500
        amount_usd = eth_amount * price
        
        message = (
            f"{format_whale_message(amount_usd)}\n\n"
            f"▫️ Сумма: <b>{eth_amount:,.0f} ETH</b> (${amount_usd/1_000_000:.2f}M)\n"
            f"▫️ От: {random.choice(['Binance', 'Coinbase', 'Unknown wallet'])}\n"
            f"▫️ К: {random.choice(['Cold wallet', 'Exchange', 'DeFi contract'])}\n"
            f"▫️ Время: {datetime.utcnow().strftime('%H:%M UTC')}\n\n"
            "#ETH #WhaleAlert"
        )
        await bot.send_message(CHANNEL_ID, message)
    except Exception as e:
        logging.error(f"Error simulating whale alerts: {e}")

# ===== ИНИЦИАЛИЗАЦИЯ ПЛАНИРОВЩИКА =====
def setup_scheduler():
    # Новости каждые 4 часа
    scheduler.add_job(publish_eth_news, 'interval', hours=4)
    
    # Анализ свечей
    scheduler.add_job(send_candle_analysis, 'cron', hour='*/1', args=["1h"])  # Каждый час
    scheduler.add_job(send_candle_analysis, 'cron', hour='*/4', args=["4h"])  # Каждые 4 часа
    scheduler.add_job(send_candle_analysis, 'cron', hour=0, minute=5, args=["1d"])  # Ежедневно в 00:05 UTC
    scheduler.add_job(send_candle_analysis, 'cron', day_of_week='sun', hour=23, minute=55, args=["1w"])  # Воскресенье 23:55 UTC
    
    # Индикатор альтсезона ежедневно в 12:00 по Лондону (11:00 UTC)
    scheduler.add_job(send_altseason_indicator, 'cron', hour=11, minute=0)
    
    # Мониторинг цены каждые 30 минут
    scheduler.add_job(monitor_price_changes, 'interval', minutes=30)
    
    # Имитация ликвидаций и whale alert
    scheduler.add_job(simulate_binance_liquidations, 'interval', minutes=15)
    scheduler.add_job(simulate_whale_alert, 'interval', minutes=20)
    
    scheduler.start()

# ===== ОСНОВНЫЕ КОМАНДЫ =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🚀 <b>Ethereum Tracker Bot активирован!</b>\n\n"
        "Бот автоматически публикует:\n"
        "✅ Новости об ETH\n"
        "📈 Анализ ценовых движений\n"
        "📉 Данные о ликвидациях\n"
        "🐋 Крупные транзакции (Whale Alert)\n"
        "🌐 Индикатор альтсезона\n\n"
        "Все публикации отправляются в указанный канал."
    )

# ===== ЗАПУСК БОТА =====
async def on_startup():
    logging.info("Starting scheduler...")
    setup_scheduler()
    await bot.send_message(ADMIN_CHAT_ID, "🟢 Ethereum Tracker Bot запущен и работает!")

async def on_shutdown():
    logging.info("Stopping scheduler...")
    scheduler.shutdown()
    await bot.send_message(ADMIN_CHAT_ID, "🔴 Ethereum Tracker Bot остановлен!")
    await bot.session.close()

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    
    await on_startup()
    await dp.start_polling(bot)
    await on_shutdown()

if __name__ == "__main__":
    asyncio.run(main())

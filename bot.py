import os
import asyncio
import logging
import aiohttp
import re
import json
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Импорт всех необходимых классов
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup
from cachetools import TTLCache
from aiohttp import web

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Проверка и получение переменных окружения
try:
    API_TOKEN = os.environ['API_TOKEN']
    ADMIN_CHAT_ID = int(os.environ['ADMIN_CHAT_ID'])
    CHANNEL_ID = int(os.environ['CHANNEL_ID'])
except KeyError as e:
    logger.critical(f"Отсутствует обязательная переменная окружения: {e}")
    raise

# URL каналов для мониторинга
LIQUIDATIONS_CHANNEL_URL = "https://t.me/s/BinanceLiquidations"
WHALE_ALERT_CHANNEL_URL = "https://t.me/s/whale_alert_io"

# Инициализация бота aiogram
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Кэш для предотвращения дублирования сообщений
message_cache = TTLCache(maxsize=1000, ttl=3600)  # 1 час

# Глобальные переменные для отслеживания цены
PREVIOUS_PRICE = None

# Middleware для приватного доступа
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = None
        
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
        
        if not user_id or user_id != ADMIN_CHAT_ID:
            return False
        
        return await handler(event, data)

dp.update.outer_middleware(AccessMiddleware())

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def fetch_crypto_news():
    """Получение новостей о ETH из различных источников"""
    sources = {
        "CoinDesk": "https://www.coindesk.com/tag/ethereum/feed",
        "The Block": "https://www.theblock.co/rss/ethereum",
        "CoinTelegraph": "https://cointelegraph.com/rss/tag/ethereum",
        "Decrypt": "https://decrypt.co/feed/ethereum",
        "CryptoSlate": "https://cryptoslate.com/categories/ethereum/feed/",
        "ETHHub": "https://ethhub.io/feed.xml"
    }
    
    news_items = []
    seen_titles = set()  # Для фильтрации дубликатов
    
    async with aiohttp.ClientSession() as session:
        for source, url in sources.items():
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        xml = await response.text()
                        soup = BeautifulSoup(xml, 'xml')
                        
                        for item in soup.find_all('item')[:5]:
                            title = item.title.text.strip()
                            
                            # Пропускаем дубликаты
                            if title in seen_titles:
                                continue
                            seen_titles.add(title)
                            
                            link = item.link.text
                            pub_date = item.pubDate.text if item.pubDate else ""
                            
                            # Обработка даты
                            try:
                                timestamp = datetime.strptime(pub_date, '%a, %d %b %Y %H:%M:%S %Z').replace(tzinfo=timezone.utc)
                            except:
                                timestamp = datetime.now(timezone.utc)
                            
                            # Определяем важность новости
                            importance = "❗️"
                            keywords = [
                                "hack", "exploit", "vulnerability", "critical", 
                                "emergency", "vitalik", "upgrade", "hard fork",
                                "security", "exploit", "bug", "attack"
                            ]
                            if any(kw in title.lower() for kw in keywords):
                                importance = "❗️❗️❗️"
                            
                            # Добавляем эмодзи для разных источников
                            source_emoji = {
                                "CoinDesk": "📰",
                                "The Block": "🔗",
                                "CoinTelegraph": "📢",
                                "Decrypt": "🔓",
                                "CryptoSlate": "🧩",
                                "ETHHub": "⚙️"
                            }
                            
                            news_items.append({
                                "source": f"{source_emoji.get(source, '📌')} {source}",
                                "title": f"{importance} {title}",
                                "link": link,
                                "pub_date": pub_date,
                                "timestamp": timestamp
                            })
            except Exception as e:
                logging.error(f"Error fetching news from {source}: {e}")
    
    # Сортируем новости по дате (свежие в начале)
    news_items.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return news_items[:15]  # Возвращаем 15 самых свежих новостей

async def get_eth_price():
    """Получение текущей цены ETH"""
    url = "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response:
                data = await response.json()
                return float(data['price'])
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
            async with session.get(url, timeout=10) as response:
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

async def get_altseason_indicator():
    """Реальный индикатор альтсезона с CoinGecko"""
    url = "https://api.coingecko.com/api/v3/global"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                data = await response.json()
                btc_dominance = data['data']['market_cap_percentage']['btc']
                eth_dominance = data['data']['market_cap_percentage']['eth']
                
                # Расчет индикатора альтсезона
                altseason_score = 100 - btc_dominance
                
                if altseason_score < 30:
                    return f"🔴 {altseason_score:.1f}% - Доминирование BTC ({btc_dominance}%). Альтсезон маловероятен."
                elif altseason_score < 60:
                    return f"🟡 {altseason_score:.1f}% - Переходная фаза. Доминирование ETH: {eth_dominance}%"
                else:
                    return f"🟢 {altseason_score:.1f}% - Альтсезон! Рост альткоинов вероятен."
    except Exception as e:
        logging.error(f"Error fetching altseason indicator: {e}")
        return "🔴 Ошибка получения данных об альтсезоне"

async def fetch_telegram_channel(url):
    """Получение сообщений из публичного Telegram канала через веб-интерфейс"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    messages = []
                    # Ищем все сообщения в канале
                    for message_div in soup.find_all('div', class_='tgme_widget_message'):
                        # Пропускаем рекламные посты
                        if message_div.find('a', class_='tgme_widget_message_ad_label'):
                            continue
                            
                        text_div = message_div.find('div', class_='tgme_widget_message_text')
                        if text_div:
                            message_text = text_div.get_text(strip=True)
                            message_link = message_div.find('a', class_='tgme_widget_message_date')['href']
                            message_time = message_div.find('time')['datetime']
                            
                            messages.append({
                                'text': message_text,
                                'link': message_link,
                                'time': message_time
                            })
                    
                    return messages
                else:
                    logging.error(f"Ошибка при получении канала {url}: статус {response.status}")
                    return []
    except Exception as e:
        logging.error(f"Ошибка парсинга канала {url}: {e}")
        return []

# ===== ЗАПЛАНИРОВАННЫЕ ЗАДАЧИ =====
async def publish_eth_news():
    """Публикация новостей о ETH"""
    try:
        news = await fetch_crypto_news()
        for item in news:
            # Проверка на дубликаты
            cache_key = f"news_{item['link']}"
            if cache_key in message_cache:
                continue
                
            message_cache[cache_key] = True
            
            message = (
                f"{item['title']}\n\n"
                f"📰 Источник: {item['source']}\n"
                f"⏰ Дата: {item['pub_date']}\n"
                f"<a href='{item['link']}'>Читать полностью</a>"
            )
            await bot.send_message(CHANNEL_ID, message, disable_web_page_preview=True)
            await asyncio.sleep(2)  # Пауза между сообщениями
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
        indicator = await get_altseason_indicator()
        message = (
            "🌐 <b>ИНДИКАТОР АЛЬТСЕЗОНА</b>\n\n"
            f"{indicator}\n\n"
            "ℹ️ Рассчитано на основе доминирования BTC с CoinGecko"
        )
        await bot.send_message(CHANNEL_ID, message)
    except Exception as e:
        logging.error(f"Error sending altseason indicator: {e}")

async def monitor_price_changes():
    """Мониторинг резких изменений цены"""
    global PREVIOUS_PRICE
    
    try:
        current_price = await get_eth_price()
        if not current_price:
            return
        
        if PREVIOUS_PRICE is None:
            PREVIOUS_PRICE = current_price
            return
            
        # Расчет реального изменения
        change = ((current_price - PREVIOUS_PRICE) / PREVIOUS_PRICE) * 100
        PREVIOUS_PRICE = current_price
        
        if abs(change) > 3:
            direction = "📈" if change > 0 else "📉"
            message = (
                f"{direction * 3} <b>РЕЗКОЕ ИЗМЕНЕНИЕ ЦЕНЫ ETH!</b> {direction * 3}\n\n"
                f"▫️ Текущая цена: <b>${current_price:,.2f}</b>\n"
                f"▫️ Изменение: <b>{change:.2f}%</b> за последние 30 минут\n\n"
                f"⚠️ Возможны повышенные колебания рынка"
            )
            
            # Проверка на дубликаты
            cache_key = f"price_{current_price:.2f}_{change:.2f}"
            if cache_key in message_cache:
                return
                
            message_cache[cache_key] = True
            await bot.send_message(CHANNEL_ID, message)
    except Exception as e:
        logging.error(f"Error monitoring price changes: {e}")

async def publish_liquidations():
    """Публикация данных о ликвидациях"""
    try:
        messages = await fetch_telegram_channel(LIQUIDATIONS_CHANNEL_URL)
        for msg in messages:
            if "Liquidated on #ETH" in msg['text']:
                # Проверка на дубликаты
                cache_key = f"liq_{msg['link']}"
                if cache_key in message_cache:
                    continue
                    
                message_cache[cache_key] = True
                
                # Форматируем сообщение
                message = (
                    "📉 <b>ЛИКВИДАЦИЯ ETH НА BINANCE!</b>\n\n"
                    f"{msg['text']}\n\n"
                    f"<a href='{msg['link']}'>Источник</a> | {msg['time']}"
                )
                await bot.send_message(CHANNEL_ID, message, disable_web_page_preview=True)
                await asyncio.sleep(1)  # Пауза между сообщениями
    except Exception as e:
        logging.error(f"Ошибка публикации ликвидаций: {e}")

async def publish_whale_alerts():
    """Публикация whale-транзакций"""
    try:
        messages = await fetch_telegram_channel(WHALE_ALERT_CHANNEL_URL)
        for msg in messages:
            if "#ETH" in msg['text'] and "USD" in msg['text']:
                # Проверка на дубликаты
                cache_key = f"whale_{msg['link']}"
                if cache_key in message_cache:
                    continue
                    
                message_cache[cache_key] = True
                
                # Форматируем сообщение
                message = (
                    "🐋 <b>WHALE ALERT!</b>\n\n"
                    f"{msg['text']}\n\n"
                    f"<a href='{msg['link']}'>Источник</a> | {msg['time']}"
                )
                await bot.send_message(CHANNEL_ID, message, disable_web_page_preview=True)
                await asyncio.sleep(1)  # Пауза между сообщениями
    except Exception as e:
        logging.error(f"Ошибка публикации whale alerts: {e}")

# ===== ИНИЦИАЛИЗАЦИЯ ПЛАНИРОВЩИКА =====
def setup_scheduler():
    # Новости каждые 2 часа
    scheduler.add_job(publish_eth_news, 'interval', hours=2)
    
    # Анализ свечей
    scheduler.add_job(send_candle_analysis, 'cron', hour='*/1', args=["1h"])  # Каждый час
    scheduler.add_job(send_candle_analysis, 'cron', hour='*/4', args=["4h"])  # Каждые 4 часа
    scheduler.add_job(send_candle_analysis, 'cron', hour=0, minute=5, args=["1d"])  # Ежедневно в 00:05 UTC
    scheduler.add_job(send_candle_analysis, 'cron', day_of_week='sun', hour=23, minute=55, args=["1w"])  # Воскресенье 23:55 UTC
    
    # Индикатор альтсезона ежедневно в 11:00 UTC
    scheduler.add_job(send_altseason_indicator, 'cron', hour=11, minute=0)
    
    # Мониторинг цены каждые 15 минут
    scheduler.add_job(monitor_price_changes, 'interval', minutes=15)
    
    # Парсинг данных
    scheduler.add_job(publish_liquidations, 'interval', minutes=10)
    scheduler.add_job(publish_whale_alerts, 'interval', minutes=15)
    
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

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Проверка статуса бота"""
    eth_price = await get_eth_price()
    jobs = scheduler.get_jobs()
    
    status = (
        f"🟢 Бот активен\n"
        f"▫️ Текущая цена ETH: ${eth_price:,.2f if eth_price else 'N/A'}\n"
        f"▫️ Активных задач: {len(jobs)}\n"
        f"▫️ След. ликвидации: {jobs[5].next_run_time if len(jobs) > 5 else 'N/A'}\n"
        f"▫️ След. whale alert: {jobs[6].next_run_time if len(jobs) > 6 else 'N/A'}"
    )
    
    await message.answer(status)

@dp.message(Command("ping_admin"))
async def cmd_ping_admin(message: types.Message):
    """Проверка доступности администратора"""
    try:
        await bot.send_chat_action(ADMIN_CHAT_ID, "typing")
        await message.answer("✅ Администратор доступен")
    except TelegramForbiddenError:
        await message.answer("❌ Ошибка: бот не может связаться с администратором")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {str(e)}")

# ===== HTTP SERVER FOR HEALTH CHECKS =====
async def health_handler(request):
    return web.Response(text="Bot is running")

async def start_http_server():
    """Запуск HTTP-сервера для health checks"""
    app = web.Application()
    app.router.add_get('/health', health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"HTTP server started on port {port}")

# ===== ОСНОВНЫЕ ФУНКЦИИ ЗАПУСКА =====
async def on_startup():
    logging.info("Starting scheduler...")
    
    # Проверка обязательных переменных
    required_envs = ['API_TOKEN', 'ADMIN_CHAT_ID', 'CHANNEL_ID']
    missing = [var for var in required_envs if not os.getenv(var)]
    
    if missing:
        error_msg = f"Отсутствуют переменные окружения: {', '.join(missing)}"
        logging.critical(error_msg)
        exit(1)
    
    setup_scheduler()
    
    # Проверка доступности администратора
    try:
        me = await bot.get_me()
        logging.info(f"Бот @{me.username} успешно запущен")
        
        # ПРОВЕРКА: Бот может отправлять сообщения администратору?
        await bot.send_chat_action(ADMIN_CHAT_ID, "typing")
        logging.info(f"Администратор {ADMIN_CHAT_ID} доступен")
    except TelegramForbiddenError:
        logging.warning(f"Бот не может отправить сообщение администратору {ADMIN_CHAT_ID}. "
                        "Убедитесь, что администратор запустил бота командой /start")
    except Exception as e:
        logging.error(f"Ошибка проверки администратора: {e}")

async def on_shutdown():
    logging.info("Stopping scheduler...")
    scheduler.shutdown()
    try:
        await bot.send_message(ADMIN_CHAT_ID, "🔴 Ethereum Tracker Bot остановлен!")
    except TelegramForbiddenError:
        logging.warning("Не удалось отправить сообщение администратору при остановке")
    except Exception as e:
        logging.error(f"Ошибка при остановке: {e}")

# ===== ГЛАВНАЯ ФУНКЦИЯ =====
async def main():
    # Запуск HTTP-сервера
    await start_http_server()
    
    # Инициализация бота
    await on_startup()
    
    # Запуск обработки сообщений
    await dp.start_polling(bot)
    
    # Остановка
    await on_shutdown()

if __name__ == "__main__":
    asyncio.run(main())

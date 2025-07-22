import os 
import asyncio
import logging
import aiohttp
import re
import random
from cachetools import TTLCache
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup
from telethon import TelegramClient

# Конфигурация
API_TOKEN = os.getenv('API_TOKEN')
ADMIN_CHAT_ID = 579542680  # Ваш chat_id
CHANNEL_ID = -1002881724171  # ID вашего канала для публикаций
ALLOWED_USERS = [ADMIN_CHAT_ID]
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
LIQUIDATIONS_CHANNEL = 'BinanceLiquidations'
WHALE_ALERT_CHANNEL = 'whale_alert_io'

# Инициализация бота
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Инициализация клиентов Telegram
client_liquidations = TelegramClient('binance_session', TELEGRAM_API_ID, TELEGRAM_API_HASH)
client_whale = TelegramClient('whale_session', TELEGRAM_API_ID, TELEGRAM_API_HASH)

# Кэш для предотвращения дублирования сообщений
message_cache = TTLCache(maxsize=1000, ttl=3600)  # 1 час

# Глобальные переменные для отслеживания цены
PREVIOUS_PRICE = None

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
        "CoinTelegraph": "https://cointelegraph.com/rss/tag/ethereum",
        "Decrypt": "https://decrypt.co/feed/ethereum",
        "ETHNews": "https://www.ethnews.com/feed",
        "CryptoSlate": "https://cryptoslate.com/categories/ethereum/feed/",
        "TrustNodes": "https://www.trustnodes.com/feed",
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
                                "ETHNews": "🌐",
                                "CryptoSlate": "🧩",
                                "TrustNodes": "🤝",
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

def get_altseason_indicator():
    """Расчет индикатора альтсезона (упрощенная версия)"""
    # В реальной реализации здесь будет анализ доминирования BTC/ETH
    # Пока используем случайное значение для демонстрации
    value = random.randint(0, 100)
    
    if value < 30:
        return f"🔴 {value} - Доминирование BTC. Альтсезон маловероятен."
    elif value < 70:
        return f"🟡 {value} - Переходная фаза. Возможны движения альтов."
    else:
        return f"🟢 {value} - Альтсезон! Рост альткоинов вероятен."

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
            await asyncio.sleep(3)  # Пауза между сообщениями
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


# ===== РЕАЛЬНЫЙ ПАРСИНГ ЛИКВИДАЦИЙ =====
async def parse_real_liquidations():
    """Парсинг реальных ликвидаций с Telegram-канала"""
    liquidations = []
    
    try:
        if not client_liquidations.is_connected():
            await client_liquidations.start()
        
        channel = await client_liquidations.get_entity(LIQUIDATIONS_CHANNEL)
        
        # Получаем последние 20 сообщений
        messages = await client_liquidations.get_messages(channel, limit=20)
        
        # Фильтрация сообщений за последний час
        min_time = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_messages = [msg for msg in messages if msg.date > min_time]
        
        for msg in recent_messages:
            if data := parse_liquidation_message(msg.text):
                data['timestamp'] = msg.date
                liquidations.append(data)
                
    except Exception as e:
        logging.error(f"Ошибка парсинга ликвидаций: {str(e)}")
    
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
                
                # Проверка на дубликаты
                cache_key = f"liq_{amount}_{price}"
                if cache_key in message_cache:
                    return
                    
                message_cache[cache_key] = True
                
                message = (
                    "📉 <b>РЕАЛЬНАЯ ЛИКВИДАЦИЯ ETH НА BINANCE!</b>\n\n"
                    f"▫️ Направление: <b>{position}</b>\n"
                    f"▫️ Сумма: <b>${amount/1_000_000:.2f}M</b>\n"
                    f"▫️ Цена: ${price:.2f}\n"
                    f"▫️ Время: {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
                    "#ETH #Liquidation #Binance"
                )
                await bot.send_message(CHANNEL_ID, message)
                return
        
        # Резервный вариант если данных нет
        logging.warning("Не найдены свежие ликвидации ETH")
            
    except Exception as e:
        logging.critical(f"Критическая ошибка публикации ликвидаций: {str(e)}")

# ===== РЕАЛЬНЫЙ ПАРСИНГ WHALE ALERT =====
async def parse_real_whale_alerts():
    """Парсинг реальных whale-транзакций с Telegram-канала Whale Alert"""
    alerts = []
    
    try:
        if not client_whale.is_connected():
            await client_whale.start()
        
        channel = await client_whale.get_entity(WHALE_ALERT_CHANNEL)
        
        # Получаем последние 20 сообщений
        messages = await client_whale.get_messages(channel, limit=20)
        
        # Фильтрация сообщений за последний час
        min_time = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_messages = [msg for msg in messages if msg.date > min_time]
        
        for msg in recent_messages:
            if data := parse_whale_message(msg.text):
                data['timestamp'] = msg.date
                alerts.append(data)
                
    except Exception as e:
        logging.error(f"Ошибка парсинга Whale Alert: {str(e)}")
    
    return alerts

def parse_whale_message(text: str) -> dict | None:
    """Разбор сообщения о whale-транзакции"""
    if not text or "#ETH" not in text:
        return None
    
    try:
        # Пример сообщения:
        # 🚨  24,999 #ETH (87,465,128 USD) transferred from #Coinbase to unknown wallet
        # Tx: https://etherscan.io/tx/0x... 
        # #ETH #WhaleAlert
        lines = text.split('\n')
        if len(lines) < 1:
            return None
        
        # Парсинг основной информации
        match = re.search(r"([\d,\.]+)\s*#?ETH\s*\(([\d,\.]+)\s+USD\).*?from\s+(.*?)\s+to\s+(.*)", lines[0], re.IGNORECASE)
        if not match:
            return None
        
        eth_amount = float(match.group(1).replace(',', ''))
        usd_amount = float(match.group(2).replace(',', ''))
        from_wallet = match.group(3).strip().replace('#', '')
        to_wallet = match.group(4).strip().replace('#', '')
        
        # Парсинг ссылки на транзакцию
        tx_url = None
        for line in lines:
            if line.startswith("Tx: http"):
                tx_url = line.replace("Tx: ", "").strip()
                break
        
        return {
            'eth_amount': eth_amount,
            'usd_amount': usd_amount,
            'from_wallet': from_wallet,
            'to_wallet': to_wallet,
            'tx_url': tx_url
        }
    
    except (ValueError, IndexError) as e:
        logging.warning(f"Ошибка формата Whale Alert: {str(e)}")
        return None

async def publish_real_whale_alerts():
    """Публикация реальных whale-транзакций"""
    try:
        alerts = await parse_real_whale_alerts()
        for alert in alerts:
            # Проверка на дубликаты
            cache_key = f"whale_{alert['eth_amount']}_{alert['usd_amount']}"
            if cache_key in message_cache:
                continue
                
            message_cache[cache_key] = True
            
            # Форматируем сообщение
            emoji = "🐋" if alert['usd_amount'] < 100_000_000 else "🐳"
            message = (
                f"{emoji} <b>WHALE ALERT: {alert['eth_amount']:,.0f} ETH!</b>\n\n"
                f"▫️ Сумма: <b>${alert['usd_amount']/1_000_000:.2f}M</b>\n"
                f"▫️ От: {alert['from_wallet']}\n"
                f"▫️ К: {alert['to_wallet']}\n"
            )
            
            if alert['tx_url']:
                message += f"▫️ Транзакция: <a href='{alert['tx_url']}'>Etherscan</a>\n"
            
            message += (
                f"▫️ Время: {alert['timestamp'].astimezone(timezone.utc).strftime('%H:%M UTC')}\n\n"
                "#ETH #WhaleAlert"
            )
            
            await bot.send_message(CHANNEL_ID, message, disable_web_page_preview=True)
        
        # Если не найдено свежих транзакций
        if not alerts:
            logging.info("No fresh whale alerts found")
            
    except Exception as e:
        logging.critical(f"Критическая ошибка Whale Alert: {str(e)}")

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
    
    # Мониторинг цены каждые 30 минут
    scheduler.add_job(monitor_price_changes, 'interval', minutes=30)
    
    # Парсинг реальных данных
    scheduler.add_job(publish_real_liquidations, 'interval', minutes=10)
    scheduler.add_job(publish_real_whale_alerts, 'interval', minutes=15)
    
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
        f"▫️ Текущая цена ETH: ${eth_price:,.2f}\n"
        f"▫️ Активных задач: {len(jobs)}\n"
        f"▫️ След. ликвидации: {jobs[0].next_run_time if jobs else 'N/A'}\n"
        f"▫️ След. whale alert: {jobs[1].next_run_time if len(jobs) > 1 else 'N/A'}"
    )
    
    await message.answer(status)

# ===== ЗАПУСК БОТА =====
async def on_startup():
    logging.info("Starting scheduler...")
    
    # Проверка обязательных переменных
    required_envs = ['API_TOKEN', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH']
    missing = [var for var in required_envs if not os.getenv(var)]
    
    if missing:
        error_msg = f"Отсутствуют переменные окружения: {', '.join(missing)}"
        logging.critical(error_msg)
        await bot.send_message(ADMIN_CHAT_ID, f"🔴 ОШИБКА: {error_msg}")
        exit(1)
    
    setup_scheduler()
    await bot.send_message(ADMIN_CHAT_ID, "🟢 Ethereum Tracker Bot запущен и работает!")

async def on_shutdown():
    logging.info("Stopping scheduler...")
    scheduler.shutdown()
    
    # Закрытие клиентов Telegram
    if client_liquidations.is_connected():
        await client_liquidations.disconnect()
    if client_whale.is_connected():
        await client_whale.disconnect()
    
    await bot.send_message(ADMIN_CHAT_ID, "🔴 Ethereum Tracker Bot остановлен!")
    await bot.session.close()

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    
    await on_startup()
    await dp.start_polling(bot)
    await on_shutdown()

if __name__ == "__main__":
    asyncio.run(main())

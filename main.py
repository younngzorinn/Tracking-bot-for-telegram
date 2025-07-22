import logging
import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.types import ParseMode
from aiogram.utils import executor
from datetime import datetime

API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")  # Установи переменную окружения
BOT_USERNAME = "EthereumTrackerNewsUpdates_bot"

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO)

NEWS_SOURCES = [
    "https://rss.app/feeds/TrK9U72kbG7ExS7s.xml",  # CoinDesk ETH
    "https://rss.app/feeds/IxaMDb7bW79A0vUj.xml"   # Cointelegraph ETH
]

ETH_HASHTAGS = ["#ETH", "Ethereum"]
LAST_SENT_NEWS = set()

async def fetch_rss():
    async with aiohttp.ClientSession() as session:
        for url in NEWS_SOURCES:
            try:
                async with session.get(url) as resp:
                    text = await resp.text()
                    items = text.split("<item>")[1:5]
                    for item in items:
                        title = item.split("<title>")[1].split("</title>")[0]
                        link = item.split("<link>")[1].split("</link>")[0]
                        guid = item.split("<guid>")[1].split("</guid>")[0]
                        
                        if guid not in LAST_SENT_NEWS:
                            LAST_SENT_NEWS.add(guid)
                            is_important = any(x in title for x in ["ETF", "BlackRock", "SEC", "whale"])
                            emoji = "❗️❗️❗️" if is_important else ""
                            msg = f"{emoji} <b>{title}</b>\n<a href=\"{link}\">Подробнее</a>"
                            await bot.send_message(chat_id=os.getenv("OWNER_ID"), text=msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logging.warning(f"RSS error: {e}")

@dp.message_handler(commands=["start"])
async def send_welcome(message: types.Message):
    await message.reply("Ethereum Tracker активен. Оповещения будут приходить сюда.")

async def scheduler():
    while True:
        await fetch_rss()
        await asyncio.sleep(300)  # каждые 5 минут

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(scheduler())
    executor.start_polling(dp, skip_updates=True)

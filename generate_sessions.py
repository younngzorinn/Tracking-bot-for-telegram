from telethon.sessions import StringSession
from telethon import TelegramClient
import asyncio

# ВАЖНО: Замените эти значения на свои!
API_ID = 21162944  # Ваш Telegram API ID (число)
API_HASH = 'c2090f0adf0f0ae57199a2102f2a00c7'  # Ваш Telegram API HASH (строка)

async def generate_session(name):
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        session_string = client.session.save()
        print(f"\n{name}_SESSION:", session_string)
        print(f"Сохраните эту строку в .env файл как {name}_SESSION")

if __name__ == '__main__':
    print("Генерация сессий для бота...")
    print("При появлении запроса введите номер телефона и код из Telegram")
    
    loop = asyncio.get_event_loop()
    
    print("\n1. Генерация сессии для ликвидаций...")
    loop.run_until_complete(generate_session("LIQUIDATIONS"))
    
    print("\n2. Генерация сессии для Whale Alert...")
    loop.run_until_complete(generate_session("WHALE_ALERT"))
import aiohttp
import asyncio
import ssl
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY


async def check_network():
    try:
        # Проверка базового подключения
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.yookassa.ru/v3/', ssl=ssl.SSLContext()) as response:
                print(f"✅ API доступен. Status: {response.status}")

        # Проверка аутентификации
        async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)) as session:
            async with session.get('https://api.yookassa.ru/v3/payments', ssl=ssl.SSLContext()) as response:
                if response.status == 200:
                    print("✅ Аутентификация успешна")
                else:
                    print(f"❌ Ошибка аутентификации: {response.status}")

        return True
    except Exception as e:
        print(f"❌ Ошибка сети: {e}")
        return False


asyncio.run(check_network())

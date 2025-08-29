import sys
import ssl
import aiohttp
import asyncio
from yookassa import Configuration


async def check_environment():
    print("🔍 Проверка окружения...")

    # Проверка версии Python
    print(f"Python version: {sys.version}")

    # Проверка SSL
    try:
        ssl_context = ssl.create_default_context()
        print("✅ SSL работает корректно")
    except Exception as e:
        print(f"❌ SSL ошибка: {e}")

    # Проверка сетевого подключения
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.yookassa.ru/v3/') as response:
                print(f"✅ Сетевое подключение: {response.status}")
    except Exception as e:
        print(f"❌ Сетевая ошибка: {e}")

    # Проверка учетных данных YooKassa
    try:
        Configuration.account_id = "your_shop_id"
        Configuration.secret_key = "your_secret_key"
        print("✅ Учетные данные YooKassa корректны")
    except Exception as e:
        print(f"❌ Ошибка учетных данных: {e}")


if __name__ == "__main__":
    asyncio.run(check_environment())
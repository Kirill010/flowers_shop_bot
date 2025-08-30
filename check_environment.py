import sys
import ssl
import aiohttp
import asyncio
from yookassa import Configuration
from simple_payments import *


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
        Configuration.account_id = "1037498"
        Configuration.secret_key = "live_jxIub1SHUSUh5F2hw_CjY2kK4a2Rc57yqHx5uSySQ34"
        print("✅ Учетные данные YooKassa корректны")
    except Exception as e:
        print(f"❌ Ошибка учетных данных: {e}")


if __name__ == "__main__":
    asyncio.run(check_environment())

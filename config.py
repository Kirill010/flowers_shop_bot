import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token")

# ID администратора
ADMIN_ID = int(os.getenv("ADMIN_ID", "1095668090"))
ADMIN_ID1 = int(os.getenv("ADMIN_ID1", "6643553268"))
ADMIN_ID2 = int(os.getenv("ADMIN_ID2", "905582217"))
ADMINS = [ADMIN_ID, ADMIN_ID1, ADMIN_ID2]

# ЮKassa
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "your_shop_id")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "your_secret_key")

# Остальные настройки
YOOKASSA_TAX_RATE = int(os.getenv("YOOKASSA_TAX_RATE", "1"))
YOOKASSA_TAX_SYSTEM = int(os.getenv("YOOKASSA_TAX_SYSTEM", "1"))
DB_PATH = os.getenv("DB_PATH", "data/florist.db")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# Создаем необходимые директории
Path("data").mkdir(exist_ok=True)
Path("ssl").mkdir(exist_ok=True)
Path("certificates").mkdir(exist_ok=True)

# Информация о магазине
SHOP_INFO = {
    "name": "Цветочная лавка",
    "address": "1-й Вешняковский пр., 2А, Москва",
    "phone": "+7 (965) 230-17-29",
    "work_hours": "Ежедневно с 8:00 до 20:00"
}

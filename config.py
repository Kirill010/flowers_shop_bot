# config.py
import os
from pathlib import Path
import sys
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "8201157651:AAHIA7J5_NuRHhBmEEgB1H0vuXK4DnqBucI")

# ID администратора
ADMIN_ID = int(os.getenv("ADMIN_ID", "1095668090"))
ADMIN_ID1 = int(os.getenv("ADMIN_ID1", "6643553268"))
ADMIN_ID2 = int(os.getenv("ADMIN_ID2", "905582217"))
ADMINS = [ADMIN_ID, ADMIN_ID1, ADMIN_ID2]

# ЮKassa
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "1037498")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "live_jxIub1SHUSUh5F2hw_CjY2kK4a2Rc57yqHx5uSySQ34")

# Webhook настройки
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")  # Будет установлен автоматически через ngrok
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "flowersup123")

# Порт для вебхука
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))

# Остальные настройки...
DB_PATH = os.getenv("DB_PATH", "data/florist.db")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# Создаем необходимые директории
Path("data").mkdir(exist_ok=True)
Path("ssl").mkdir(exist_ok=True)
Path("certificates").mkdir(exist_ok=True)

# Информация о магазине
SHOP_INFO = {
    "name": "Лавка цветочная история",
    "address": "1-й Вешняковский пр., 2А, Москва",
    "phone": "+7 (965) 230-17-29",
    "work_hours": "Ежедневно с 8:00 до 20:00"
}

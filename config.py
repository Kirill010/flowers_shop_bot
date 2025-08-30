import os
from pathlib import Path
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

# ЮKassa Live
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "1037498")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "live_jxIub1SHUSUh5F2hw_CjY2kK4a2Rc57yqHx5uSySQ34")

# Настройки вебхуков
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "3001"))

# Ваш домен (ЗАМЕНИТЕ НА РЕАЛЬНЫЙ!)
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "ваш-реальный-домен.ru")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook/yookassa")
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Секрет для вебхука
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your_secure_secret_token_here")

# Остальные настройки остаются без изменений...
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

# SSL настройки (для вебхуков)
SSL_CERTIFICATE = "ssl/cert.pem" if os.path.exists("ssl/cert.pem") else None
SSL_PRIVATE_KEY = "ssl/private.key" if os.path.exists("ssl/private.key") else None

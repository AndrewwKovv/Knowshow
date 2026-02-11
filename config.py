import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_RAW = os.getenv("ADMIN_TELEGRAM_ID", "")
ADMIN_IDS = [int(id.strip()) for id in ADMIN_IDS_RAW.split(",") if id.strip()]
# NOTE: debug prints moved to main.py init_app() for visibility

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./parser.db")

# Parser Settings
PARSER_WORKERS = int(os.getenv("PARSER_WORKERS", 3))
MIN_DELAY = int(os.getenv("MIN_DELAY", 5))
MAX_DELAY = int(os.getenv("MAX_DELAY", 8))
PRODUCT_CLEANUP_DAYS = int(os.getenv("PRODUCT_CLEANUP_DAYS", 14))

# Wildberries API
WB_BASE_URL = "https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search"
WB_CATALOG_URL = "https://www.wildberries.ru/catalog"

# Selenium
CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH", "chromedriver")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
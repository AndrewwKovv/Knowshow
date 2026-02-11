import requests
import asyncio
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
import time
import uuid
import logging
import json
import os

logger = logging.getLogger(__name__)

# Файл для кеширования куков
# COOKIES_CACHE_FILE = "cookies_cache.json"
COOKIES_CACHE_FILE = os.getenv("COOKIES_CACHE_FILE", "cookies_cache.json")

class CookiesManager:
    def __init__(self):
        self.cookies = None
        self.device_id = f"site_{uuid.uuid4().hex}"
        self.last_update = 0
        self.update_interval = 3600  # обновляем куки каждый час (3600 сек)
        self.updating = False

        # Загружаем кеш если есть и он свежий
        self._load_cookies_from_cache()

        self.base_headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/121.0.6167.160 Safari/537.36'
            ),
            'Cache-Control': 'no-cache',
            'DNT': '1',
            'Priority': 'u=1, i',
            'Sec-CH-UA': '"Not_A Brand";v="99", "Chromium";v="121"',
            'Sec-CH-UA-Mobile': '?0',
            'Sec-CH-UA-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
            'X-Spa-Version': '13.15.4',
            'X-UserID': '0'
        }

    def _load_cookies_from_cache(self):
        """Загружает куки из кеш-файла если он существует и свежий (менее 1 часа)."""
        try:
            if os.path.exists(COOKIES_CACHE_FILE):
                with open(COOKIES_CACHE_FILE, 'r') as f:
                    data = json.load(f)
                    self.cookies = data.get('cookies')
                    cache_timestamp = data.get('timestamp', 0)
                    
                    # Проверяем свежесть кеша: прошло менее 1 часа?
                    current_time = time.time()
                    cache_age = current_time - cache_timestamp
                    
                    if cache_age < self.update_interval:
                        self.last_update = cache_timestamp
                        if self.cookies:
                            logger.info(f"✅ Cookies loaded from cache ({int(cache_age)}s old, {len(self.cookies)} cookies)")
                    else:
                        # Кеш старый, нужно обновить
                        self.cookies = None
                        logger.info(f"⏰ Cookies cache expired ({int(cache_age)}s old), need fresh update")
        except Exception as e:
            logger.warning(f"Could not load cookies from cache: {e}")
            self.cookies = None

    def _save_cookies_to_cache(self):
        """Сохраняет куки в кеш-файл с временной меткой."""
        try:
            if self.cookies:
                # Убедимся, что директория существует (если путь содержит директорию)
                try:
                    parent = os.path.dirname(COOKIES_CACHE_FILE)
                    if parent and not os.path.exists(parent):
                        os.makedirs(parent, exist_ok=True)
                except Exception:
                    pass

                with open(COOKIES_CACHE_FILE, 'w') as f:
                    json.dump({
                        'cookies': self.cookies,
                        'timestamp': self.last_update
                    }, f)
                logger.info(f"✅ Cookies saved to cache ({len(self.cookies)} cookies)")
        except Exception as e:
            logger.warning(f"Could not save cookies to cache: {e}")

    async def update_cookies(self, force: bool = False):
        """Обновляет куки через Selenium, если не удалось — через requests.
        
        Стратегия:
        1. Если кеш свежий (< 1 часа) - используем его
        2. Если кеш старый или отсутствует - обновляем через Selenium
        3. Если Selenium не сработал - fallback на requests
        4. Сохраняем результат в кеш
        """
        if self.updating:
            logger.warning("Cookies update already running — skipping.")
            return

        # Если куки есть и свежие (прошло менее 1 часа) - ничего не делаем,
        # за исключением случая принудительного обновления (force=True).
        if self.cookies and not self.should_update_cookies() and not force:
            logger.info("✅ Cookies are fresh, using cached version")
            return

        self.updating = True
        try:
            logger.info("🔄 Fetching fresh cookies from Selenium...")
            ok = await self._update_cookies_via_selenium()
            if ok:
                self._save_cookies_to_cache()
                logger.info("✅ Cookies updated successfully via Selenium")
                return

            logger.warning("⚠️ Selenium failed — fallback to requests...")
            await self._update_cookies_via_requests()
            
            if self.cookies:
                self._save_cookies_to_cache()
                logger.info("✅ Cookies updated successfully via requests fallback")
            else:
                logger.error("❌ Failed to update cookies via both methods")

        finally:
            self.updating = False

    async def _update_cookies_via_selenium(self):
        """Асинхронный вызов Selenium."""
        try:
            logger.info("Launching Selenium...")

            loop = asyncio.get_event_loop()
            selenium_cookies = await loop.run_in_executor(
                None,
                self._selenium_fetch_cookies
            )

            if selenium_cookies:
                self.cookies = selenium_cookies
                self.last_update = time.time()
                logger.info(f"✅ Cookies updated via Selenium: {len(self.cookies)} cookies")
                return True

            return False

        except Exception as e:
            logger.error(f"Selenium cookies update error: {e}", exc_info=True)
            return False

    def _selenium_fetch_cookies(self):
        """Синхронная загрузка куков — запускается в executor."""
        try:
            options = webdriver.ChromeOptions()

            # Новый headless — обязательный, иначе WB даёт 1 cookie
            options.add_argument("--headless=new")

            # Для контейнеров Amvera
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            # Anti-bot обход
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            # Нормальный экран браузера
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")

            # Настоящий User-Agent
            options.add_argument(
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.6167.160 Safari/537.36"
            )

            # Запуск браузера
            try:
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
            except Exception:
                driver = webdriver.Chrome(options=options)

            try:
                logger.info("🌐 Opening WB site via Selenium...")
                # Установляем большой timeout для медленного интернета
                driver.set_page_load_timeout(20)
                driver.get("https://www.wildberries.ru/")
                
                # Даём время на загрузку JS и установку cookies
                time.sleep(5)

                cookies = driver.get_cookies()
                logger.info(f"✅ Got {len(cookies)} cookies from Selenium")

                return cookies if cookies else None

            finally:
                driver.quit()

        except Exception as e:
            logger.error(f"❌ Selenium fatal error: {e}", exc_info=True)
            return None

    async def _update_cookies_via_requests(self):
        """Fallback — минимальные куки через обычный GET с retry."""
        try:
            logger.info("📡 Trying requests cookie fetch (fallback)...")
            loop = asyncio.get_event_loop()

            def fetch():
                headers = self.base_headers.copy()
                try:
                    resp = requests.get(
                        "https://www.wildberries.ru",
                        headers=headers,
                        timeout=10
                    )
                    resp.raise_for_status()
                    return [{"name": k, "value": v} for k, v in resp.cookies.items()]
                except requests.RequestException as e:
                    logger.warning(f"⚠️ Requests failed: {e}")
                    return []

            cookies = await loop.run_in_executor(None, fetch)

            if cookies:
                self.cookies = cookies
                self.last_update = time.time()
                logger.info(f"✅ Got {len(cookies)} cookies from requests")
            else:
                logger.warning("⚠️ Requests returned empty cookies")

        except Exception as e:
            logger.error(f"❌ Requests cookie error: {e}", exc_info=True)

    def should_update_cookies(self):
        """Проверяет, пора ли обновлять куки."""
        return (time.time() - self.last_update) > self.update_interval

    def get_headers(self, query=None):
        """Формирует заголовки с актуальными cookies."""
        headers = self.base_headers.copy()

        # Генерация уникального QueryID
        timestamp = int(time.time() * 1000)
        rnd = uuid.uuid4().hex[:8]
        headers["X-QueryID"] = f"qid{self.device_id.replace('site_', '')}{timestamp}{rnd}"

        headers["DeviceID"] = self.device_id

        if query:
            from urllib.parse import quote
            headers["Referer"] = f"https://www.wildberries.ru/catalog/0/search.aspx?search={quote(query)}"
        else:
            headers["Referer"] = "https://www.wildberries.ru/catalog/0/search.aspx"

        if self.cookies:
            cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in self.cookies])
            headers["Cookie"] = cookie_string
        else:
            logger.warning("No cookies available!")

        return headers

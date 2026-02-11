import requests
import asyncio
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import uuid
import logging
import json
import os
import re

logger = logging.getLogger(__name__)

COOKIES_CACHE_FILE = os.getenv("COOKIES_CACHE_FILE", "cookies_cache.json")

class CookiesManager:
    def __init__(self):
        self.cookies = None
        self.device_id = f"site_{uuid.uuid4().hex}"
        self.last_update = 0
        self.update_interval = 1800  # Уменьшили до 30 минут — токен живёт недолго
        self.updating = False

        self._load_cookies_from_cache()

        self.base_headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/143.0.0.0 Safari/537.36'
            ),
            'Cache-Control': 'no-cache',
            'DNT': '1',
            'Priority': 'u=1, i',
            'Sec-CH-UA': '"Chromium";v="143", "Not A(Brand";v="24"',
            'Sec-CH-UA-Mobile': '?0',
            'Sec-CH-UA-Platform': '"macOS"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
            'X-Spa-Version': '13.22.10',
            'X-UserID': '0'
        }

    def _load_cookies_from_cache(self):
        try:
            if os.path.exists(COOKIES_CACHE_FILE):
                with open(COOKIES_CACHE_FILE, 'r') as f:
                    data = json.load(f)
                    self.cookies = data.get('cookies')
                    cache_timestamp = data.get('timestamp', 0)
                    
                    current_time = time.time()
                    cache_age = current_time - cache_timestamp
                    
                    if cache_age < self.update_interval:
                        self.last_update = cache_timestamp
                        if self.cookies:
                            # Проверяем наличие критического x_wbaas_token
                            has_token = any(c.get('name') == 'x_wbaas_token' for c in self.cookies)
                            if has_token:
                                logger.info(f"✅ Cookies loaded from cache ({int(cache_age)}s old, {len(self.cookies)} cookies, has token)")
                            else:
                                logger.warning("Cached cookies missing x_wbaas_token, will refresh")
                                self.cookies = None
                    else:
                        self.cookies = None
                        logger.info(f"⏰ Cookies cache expired ({int(cache_age)}s old)")
        except Exception as e:
            logger.warning(f"Could not load cookies from cache: {e}")
            self.cookies = None

    def _save_cookies_to_cache(self):
        try:
            if self.cookies:
                parent = os.path.dirname(COOKIES_CACHE_FILE)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)

                with open(COOKIES_CACHE_FILE, 'w') as f:
                    json.dump({
                        'cookies': self.cookies,
                        'timestamp': self.last_update
                    }, f)
                logger.info(f"✅ Cookies saved to cache ({len(self.cookies)} cookies)")
        except Exception as e:
            logger.warning(f"Could not save cookies to cache: {e}")

    async def update_cookies(self, force: bool = False):
        if self.updating:
            logger.warning("Cookies update already running — skipping.")
            return

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

            logger.warning("⚠️ Selenium failed — trying requests fallback...")
            await self._update_cookies_via_requests()
            
            if self.cookies:
                self._save_cookies_to_cache()
                logger.info("✅ Cookies updated via requests fallback")
            else:
                logger.error("❌ Failed to update cookies via both methods")

        finally:
            self.updating = False

    async def _update_cookies_via_selenium(self):
        try:
            logger.info("Launching Selenium...")

            loop = asyncio.get_event_loop()
            selenium_cookies = await loop.run_in_executor(
                None,
                self._selenium_fetch_cookies
            )

            if selenium_cookies and len(selenium_cookies) >= 2:
                self.cookies = selenium_cookies
                self.last_update = time.time()
                return True

            return False

        except Exception as e:
            logger.error(f"Selenium cookies update error: {e}", exc_info=True)
            return False

    def _selenium_fetch_cookies(self):
        driver = None
        try:
            options = Options()

            # Критические флаги для контейнера
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            
            # Маскировка под macOS (как в вашем рабочем curl)
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--start-maximized")
            
            # Анти-детект
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            
            # Важно: отключаем DevTools чтобы не было navigator.webdriver
            options.add_argument("--remote-debugging-port=0")
            
            # User-Agent как в рабочем запросе
            options.add_argument(
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            )

            # Запускаем Chrome
            try:
                driver = webdriver.Chrome(options=options)
            except Exception as e:
                logger.error(f"Failed to start Chrome with default options: {e}")
                service = Service('/usr/local/bin/chromedriver')
                driver = webdriver.Chrome(service=service, options=options)

            # Скрываем автоматизацию через CDP
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['ru-RU', 'ru', 'en-US', 'en']
                    });
                    window.chrome = { runtime: {} };
                    window.navigator.chrome = { runtime: {} };
                '''
            })

            logger.info("🌐 Opening WB site...")
            driver.set_page_load_timeout(45)
            
            # Шаг 1: Заходим на главную
            driver.get("https://www.wildberries.ru/")
            
            # Ждём загрузки страницы (проверяем наличие body)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located(("tag name", "body"))
            )
            
            # Шаг 2: Ждём выполнения JS-челленджа (критично!)
            # x_wbaas_token появляется через 3-8 секунд после загрузки
            time.sleep(10)
            
            # Шаг 3: Скроллим чтобы активировать ленивую загрузку
            driver.execute_script("window.scrollTo(0, 500);")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, 1000);")
            time.sleep(3)
            
            # Шаг 4: Проверяем, появился ли x_wbaas_token
            cookies = driver.get_cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            
            logger.info(f"Cookies after first load: {list(cookie_dict.keys())}")
            
            # Если токена нет — пробуем кликнуть по любому элементу (имитация пользователя)
            if 'x_wbaas_token' not in cookie_dict:
                logger.warning("x_wbaas_token not found, trying user interaction...")
                try:
                    # Ищем любую ссылку и наводим на неё
                    links = driver.find_elements("tag name", "a")
                    if links:
                        driver.execute_script("arguments[0].scrollIntoView();", links[0])
                        time.sleep(1)
                        # Не кликаем, просто наводим — иногда достаточно
                        driver.execute_script("arguments[0].dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));", links[0])
                        time.sleep(3)
                        
                        # Перезагружаем куки
                        cookies = driver.get_cookies()
                        cookie_dict = {c['name']: c['value'] for c in cookies}
                        logger.info(f"Cookies after interaction: {list(cookie_dict.keys())}")
                except Exception as e:
                    logger.warning(f"Interaction failed: {e}")

            # Шаг 5: Если всё ещё нет токена — пробуем перейти на страницу поиска
            if 'x_wbaas_token' not in cookie_dict:
                logger.warning("Still no token, navigating to search page...")
                driver.get("https://www.wildberries.ru/catalog/0/search.aspx?search=iphone")
                time.sleep(8)
                cookies = driver.get_cookies()

            logger.info(f"✅ Final cookies count: {len(cookies)}")
            for c in cookies:
                logger.info(f"  - {c['name']}: {c['value'][:50]}..." if len(c['value']) > 50 else f"  - {c['name']}: {c['value']}")

            # Проверяем наличие критических куков
            critical_cookies = ['_wbauid', 'x_wbaas_token']
            present = [c['name'] for c in cookies if c['name'] in critical_cookies]
            logger.info(f"Critical cookies present: {present}")

            return cookies if cookies else None

        except Exception as e:
            logger.error(f"❌ Selenium fatal error: {e}", exc_info=True)
            return None
            
        finally:
            if driver:
                driver.quit()

    async def _update_cookies_via_requests(self):
        """Fallback — пробуем получить начальные куки через requests."""
        try:
            logger.info("📡 Trying requests cookie fetch...")
            loop = asyncio.get_event_loop()

            def fetch():
                session = requests.Session()
                headers = self.base_headers.copy()
                
                # Первый запрос — получаем _wbauid
                try:
                    resp = session.get(
                        "https://www.wildberries.ru/",
                        headers=headers,
                        timeout=15,
                        allow_redirects=True
                    )
                    
                    # Второй запрос — пробуем получить токен (редко работает без JS)
                    time.sleep(2)
                    resp2 = session.get(
                        "https://www.wildberries.ru/catalog/0/search.aspx?search=test",
                        headers=headers,
                        timeout=15
                    )
                    
                    cookies = []
                    for name, value in session.cookies.items():
                        cookies.append({
                            'name': name,
                            'value': value,
                            'domain': '.wildberries.ru',
                            'path': '/'
                        })
                    
                    return cookies
                except Exception as e:
                    logger.warning(f"Requests failed: {e}")
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
        return (time.time() - self.last_update) > self.update_interval

    def get_headers(self, query=None):
        headers = self.base_headers.copy()

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
            # Форматируем куки как в рабочем curl
            cookie_parts = []
            for c in self.cookies:
                name = c.get('name', '')
                value = c.get('value', '')
                if name and value:
                    cookie_parts.append(f"{name}={value}")
            
            headers["Cookie"] = "; ".join(cookie_parts)
            logger.debug(f"Sending cookies: {[c['name'] for c in self.cookies]}")
        else:
            logger.warning("No cookies available!")

        return headers
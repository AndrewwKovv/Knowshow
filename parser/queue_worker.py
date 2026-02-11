import asyncio
import aiohttp
import config
from datetime import datetime, timedelta
import random

class ParserQueueWorker:
    def __init__(self, num_workers=3):
        self.queue = asyncio.Queue()
        self.num_workers = num_workers
        self.last_request_time = {}
    
    async def add_task(self, product_id, search_query, keywords=None):
        """Добавляет задачу в очередь"""
        await self.queue.put({
            'product_id': product_id,
            'query': search_query,
            'keywords': keywords or []
        })
    
    async def worker(self, cookies_manager):
        """Воркер для обработки задач из очереди"""
        while True:
            task = await self.queue.get()
            
            try:
                # Пауза между запросами, берём значения из конфига
                min_d = max(1, getattr(config, 'MIN_DELAY', 30))
                max_d = max(min_d + 1, getattr(config, 'MAX_DELAY', 60))
                await asyncio.sleep(random.uniform(min_d, max_d))
                
                results = await self._parse_product(
                    task['query'], 
                    task['keywords'],
                    cookies_manager
                )
                
                yield results
                
            except Exception as e:
                print(f"Error parsing {task['query']}: {e}")
            finally:
                self.queue.task_done()
    
    async def _parse_product(self, query, keywords, cookies_manager):
        """Парсит товар с ключевыми словами"""
        base_url = 'https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search'
        
        params = {
            'query': query,
            'resultset': 'catalog',
            'sort': 'priceup',
            'page': 1
        }
        
        async with aiohttp.ClientSession() as session:
            # Первый запрос - основной товар
            async with session.get(
                base_url,
                params=params,
                headers=cookies_manager.get_headers()
            ) as resp:
                data = await resp.json()
            
            # Если есть ключевые слова - дополнительные запросы
            if keywords:
                for keyword in keywords:
                    keyword_params = {**params, **self._get_keyword_params(keyword)}
                    async with session.get(
                        base_url,
                        params=keyword_params,
                        headers=cookies_manager.get_headers()
                    ) as resp:
                        keyword_data = await resp.json()
                        data['products'].extend(keyword_data.get('products', []))
            
            return data
    
    @staticmethod
    def _get_keyword_params(keyword):
        """Маппинг ключевых слов на параметры URL"""
        keyword_map = {
            'nano-SIM+Esim': {'f4433': '830086596'},
            'Esim': {'f4433': '8047145'},
            'Esim+esim': {'f4433': '804347144'},
            'Nano-SIM': {'f4433': '469834'},
            '256GB': {'f4424': '25425'},
            '512GB': {'f4424': '117419'},
            '1TB': {'f4424': '231154'},
            'Silver': {'f14177449': '20214430;12065905'},
            'Orange': {'f14177449': '20214770'},
            'Blue': {'f14177449': '20214646'},
            'White': {'f14177449': '12065905'},
            'Black': {'f14177449': '13600062'}
        }
        return keyword_map.get(keyword, {})
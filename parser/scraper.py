import aiohttp
import json
import logging
import asyncio
from typing import List, Dict, Optional
from datetime import datetime
import re
from urllib.parse import urlencode, quote
from functools import partial

logger = logging.getLogger(__name__)

class WildberriesScraper:
    """Скрапер для Wildberries с поддержкой ключевых слов и фильтрации"""
    
    BASE_URL = "https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search"
    
    KEYWORD_MAP = {
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
    
    def __init__(self, cookies_manager):
        self.cookies_manager = cookies_manager
        self.request_count = 0
    
    async def search_product(
        self,
        query: str,
        keywords: Optional[List[str]] = None,
        exclusions: Optional[List[str]] = None
    ) -> List[Dict]:
        """Ищет товары по названию"""
        try:
            products = await self._search_by_query(query, keywords or [])
            
            if not products:
                logger.warning(f"No products found for query: {query}")
                return []
            
            products = self._filter_by_keywords(products, keywords or [])
            products = self._filter_by_exclusions(products, exclusions or [])
            
            logger.info(f"Found {len(products)} products for '{query}' after filtering")
            return products
            
        except Exception as e:
            logger.error(f"Error searching products: {e}", exc_info=True)
            return []
    
    async def _search_by_query(self, query: str, keywords: Optional[List[str]] = None, attempts: int = 0) -> List[Dict]:
        """Основной поиск товаров по названию

        - Does NOT append keywords to query text
        - If a keyword matches an entry in `KEYWORD_MAP` (normalized, case-insensitive), its
          mapping (e.g. `{'f4433': '830086596'}`) is merged into request params as filter params.
        """
        keywords = keywords or []

        params = {
            'query': query,
            'resultset': 'catalog',
            'sort': 'priceup',
            'page': 1,
            'ab_testid': 'no_promo',
            'ab_testing': 'false',
            'appType': '1',
            'curr': 'rub',
            'dest': '-1257786',
            'hide_dtype': '9;11',
            'hide_vflags': '4294967296',
            'inheritFilters': 'false',
            'lang': 'ru',
            'spp': '30',
            'suppressSpellcheck': 'false'
        }

        # Normalization helper for matching KEYWORD_MAP keys robustly
        def _norm_key(s: str) -> str:
            s = (s or '').lower()
            # keep latin and cyrillic letters and digits, remove punctuation
            s = re.sub(r'[^0-9a-zа-яё]+', ' ', s, flags=re.IGNORECASE)
            s = re.sub(r'\s+', ' ', s).strip()
            return s

        # Merge any mapped filter params from KEYWORD_MAP (normalize comparison)
        # Do NOT append keywords to query — only add filter params
        matched_keywords = []
        for kw in keywords:
            kw_str = str(kw or '').strip()
            if not kw_str:
                continue
            kw_norm = _norm_key(kw_str)
            for map_key, map_val in self.KEYWORD_MAP.items():
                if _norm_key(map_key) == kw_norm:
                    matched_keywords.append(map_key)
                    for k, v in map_val.items():
                        # If this param key already exists, append new value with ';'
                        # (will be percent-encoded to %3B later). Avoid duplicates.
                        if k in params and params[k]:
                            existing = str(params[k])
                            # split by ';' and avoid duplicate segments
                            parts = [p for p in existing.split(';') if p]
                            if v not in parts:
                                params[k] = existing + ';' + v
                        else:
                            params[k] = v

        # Use original query for headers/referrer
        headers = self.cookies_manager.get_headers(query)
        
        # Build an ordered list of parameters to match Wildberries expected ordering
        ordered_keys = [
            'ab_testing', 'ab_testid', 'appType', 'curr', 'dest',
            'hide_dtype', 'hide_vflags', 'inheritFilters', 'lang',
            'page', 'query', 'resultset', 'sort', 'spp', 'suppressSpellcheck'
        ]

        # Ensure all params are strings for urlencode
        params = {k: ('' if v is None else v) for k, v in params.items()}

        ordered_params = []
        for k in ordered_keys:
            if k in params:
                ordered_params.append((k, params.pop(k)))

        # Append remaining params (including keyword-mapped filter params) preserving insertion order
        for k, v in params.items():
            ordered_params.append((k, v))

        # Ensure fast-delivery filter (fdlvr=72 => delivery up to ~3 days) is included
        # This helps narrow results to items with quick delivery
        # ordered_params.append(('fdlvr', '120'))

        # Construct and log the full URL with query parameters using quote that preserves ';'
        try:
            safe_quote = partial(quote, safe='')
            query_string = urlencode(ordered_params, doseq=True, quote_via=safe_quote)
            full_url = f"{self.BASE_URL}?{query_string}"
        except Exception:
            # Fallback to a simple join if urlencode fails for any reason
            query_string = '&'.join([f"{k}={v}" for k, v in ordered_params])
            full_url = f"{self.BASE_URL}?{query_string}"
        
        try:
            timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            connector = aiohttp.TCPConnector(ssl=False)
            
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.get(
                    self.BASE_URL,
                    params=ordered_params,
                    headers=headers,
                    ssl=False
                ) as resp:
                    # logger.info(f"Response status: {resp.status} for query '{query}'")
                    # # Log the actual URL object used by aiohttp (percent-encoded)
                    # try:
                    #     logger.info(f"aiohttp requested URL: {resp.url}")
                    # except Exception:
                    #     pass
                    # logger.info(f"Response Content-Type: {resp.content_type}")
                    
                    if resp.status == 200:
                        # Получаем text и пробуем распарсить как JSON
                        text = await resp.text()
                        
                        if not text:
                            logger.error(f"Empty response body for query '{query}'")
                            return []
                        
                        try:
                            data = json.loads(text)
                            products = data.get('products', [])
                            return products
                        
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse JSON: {e}")
                            logger.error(f"Response preview: {text[:500]}")
                            
                            # Если это HTML (ошибка 403/429 замаскирована под 200)
                            if '<html' in text.lower() or 'blocked' in text.lower():
                                logger.warning("Got blocked response (HTML instead of JSON)")
                                # Обновляем куки и повторяем
                                # Force cookie refresh even if cache appears fresh
                                await self.cookies_manager.update_cookies(force=True)
                                await asyncio.sleep(2)
                                return await self._search_by_query(query, keywords)
                            
                            return []
                    
                    elif resp.status == 498:
                        logger.warning(f"Got 498 error, forcing cookies update and retrying...")
                        # Force cookie refresh even if cache appears fresh
                        await self.cookies_manager.update_cookies(force=True)
                        await asyncio.sleep(2)
                        
                        # Повторяем запрос с новыми куками
                        headers = self.cookies_manager.get_headers(query)
                        async with session.get(
                            self.BASE_URL,
                            params=ordered_params,
                            headers=headers,
                            ssl=False
                        ) as retry_resp:
                            if retry_resp.status == 200:
                                text = await retry_resp.text()
                                try:
                                    data = json.loads(text)
                                    products = data.get('products', [])
                                    return products
                                except json.JSONDecodeError:
                                    logger.error(f"Retry JSON decode failed")
                                    return []
                            else:
                                logger.error(f"Retry failed with status {retry_resp.status}")
                                return []
                    
                    elif resp.status == 429:
                        logger.warning("Got 429 (rate limited), waiting 60 seconds...")
                        await asyncio.sleep(60)
                        return await self._search_by_query(query, keywords)
                    
                    else:
                        logger.error(f"API returned status {resp.status} for query '{query}'")
                        text = await resp.text()
                        logger.error(f"Response: {text[:500]}")
                        return []
        
        except asyncio.TimeoutError:
            logger.error(f"Timeout while searching for '{query}' (attempts={attempts})")

            # Conservative retry strategy on timeouts:
            # - Try up to 2 attempts before giving up
            # - On first retry, try without refreshing cookies (network blip)
            # - On second retry, refresh cookies only if they are considered stale
            max_timeout_retries = 2
            if attempts < max_timeout_retries:
                try:
                    # decide whether to refresh cookies on this retry
                    if attempts == 0:
                        logger.info("Timeout occurred; retrying once without refreshing cookies")
                    else:
                        # only refresh cookies if manager deems them stale
                        try:
                            should_refresh = getattr(self.cookies_manager, 'should_update_cookies', lambda: True)()
                        except Exception:
                            should_refresh = True

                        if should_refresh:
                            logger.info("Timeout occurred; refreshing cookies before retry")
                            try:
                                await self.cookies_manager.update_cookies(force=True)
                            except Exception:
                                logger.exception("Failed to update cookies during timeout retry")
                        else:
                            logger.info("Cookies appear fresh; retrying without Selenium refresh")

                except Exception:
                    logger.exception("Error handling timeout retry logic")

                # backoff before retrying
                backoff = 2 if attempts == 0 else 5
                await asyncio.sleep(backoff)
                return await self._search_by_query(query, keywords, attempts=attempts+1)

            return []
        except Exception as e:
            logger.error(f"Error in _search_by_query: {e}", exc_info=True)
            return []
    
    def _filter_by_keywords(self, products: List[Dict], keywords: List[str]) -> List[Dict]:
        """Фильтрует товары по ключевым словам (case-insensitive)"""
        if not keywords:
            return products
        # Aggregate textual product fields for robust matching
        def _product_text(p: Dict) -> str:
            parts = []
            def collect(obj, depth=0):
                if depth > 3:
                    return
                if isinstance(obj, str):
                    parts.append(obj)
                elif isinstance(obj, dict):
                    for v in obj.values():
                        collect(v, depth + 1)
                elif isinstance(obj, list):
                    for v in obj:
                        collect(v, depth + 1)
                elif obj is None:
                    return
                else:
                    try:
                        parts.append(str(obj))
                    except Exception:
                        return
            collect(p)
            return " ".join(parts).lower()

        filtered = []
        for product in products:
            product_text = _product_text(product)
            for keyword in keywords:
                keyword_lower = str(keyword).strip().lower()
                if not keyword_lower:
                    continue
                if keyword_lower in product_text:
                    filtered.append(product)
                    break

        return filtered
    
    def _filter_by_exclusions(self, products: List[Dict], exclusions: List[str]) -> List[Dict]:
        """Фильтрует товары, исключая те, которые содержат слова из списка исключений.
        """

        if not exclusions:
            return products

        def _normalize_keep_connectors(s: str) -> str:
            s = (s or "").lower()
            s = re.sub(r'[^0-9a-zа-яё\+\-/]+', ' ', s, flags=re.IGNORECASE)
            s = re.sub(r'\s+', ' ', s).strip()
            return s

        def _compact(s: str) -> str:
            return re.sub(r'[^0-9a-zа-яё]+', '', (s or "").lower(), flags=re.IGNORECASE)

        def should_skip_esim_case(text: str) -> bool:
            """
            Возвращает True — если есть eSIM, но также указана физическая SIM.
            """
            t = text.lower()

            if "esim" in t:
                # Физическая SIM присутствует
                has_physical = any(
                    ph in t for ph in [
                        " sim",       # sim+esim
                        " nano-sim",
                        " nanosim",
                        " nano sim",
                        " physical sim",
                        " 2 sim",
                        " dual sim",      # dual sim — корректно
                        "sim +",          # sim + esim
                        "+ sim",          # esim + sim
                    ]
                )

                # Явные плохие кейсы исключаем
                bad_cases = ["esim only", "only esim", "e-sim only", "esim+esim", "dual esim"]
                if any(bad in t for bad in bad_cases):
                    return False

                if has_physical:
                    return True

            return False

        def _collect_selected_fields(product: Dict) -> List[tuple]:
            items = []
            if not isinstance(product, Dict):
                return items

            meta_root = product.get('metadata') or product.get('meta') or {}
            if isinstance(meta_root, dict):
                name_md = meta_root.get('name')
                if isinstance(name_md, str) and name_md.strip():
                    items.append(('metadata.name', name_md))

                chars = meta_root.get('characteristics') or meta_root.get('characteristicsList') or []
                if isinstance(chars, list):
                    for idx, ch in enumerate(chars):
                        if not isinstance(ch, dict):
                            continue
                        ch_name = ch.get('name')
                        if isinstance(ch_name, str) and ch_name.strip():
                            items.append((f"meta.characteristics[{idx}].name", ch_name))

                        values = ch.get('values') or ch.get('value') or ch.get('items') or []
                        if isinstance(values, list):
                            for j, val in enumerate(values):
                                if isinstance(val, dict):
                                    val_text = val.get('name') or val.get('value') or val.get('title')
                                    if isinstance(val_text, str) and val_text.strip():
                                        items.append(
                                            (f"meta.characteristics[{idx}].values[{j}]", val_text)
                                        )
                                elif isinstance(val, str):
                                    items.append(
                                        (f"meta.characteristics[{idx}].values[{j}]", val)
                                    )

            prod_name = product.get('name')
            if isinstance(prod_name, str) and prod_name.strip():
                items.append(('product.name', prod_name))

            seller = product.get('supplier') or product.get('supplierName') or product.get('brand')
            if isinstance(seller, str) and seller.strip():
                items.append(('product.supplier', seller))

            colors = product.get('colors') or []
            if isinstance(colors, list):
                for i, c in enumerate(colors):
                    if isinstance(c, dict):
                        cname = c.get('name') or c.get('title')
                        if isinstance(cname, str) and cname.strip():
                            items.append((f"product.colors[{i}].name", cname))

            return items

        filtered = []
        for product in products:
            fragments = _collect_selected_fields(product)
            has_exclusion = False

            norm_fragments = []
            for path, txt in fragments:
                norm = _normalize_keep_connectors(txt)
                tokens = norm.split() if norm else []
                compact_tokens = [_compact(t) for t in tokens]
                norm_fragments.append((path, txt, tokens, compact_tokens))

            for exclusion in exclusions:
                exclusion_lower = str(exclusion).strip().lower()
                if not exclusion_lower:
                    continue
                ex_norm = _normalize_keep_connectors(exclusion_lower)
                ex_tokens = ex_norm.split()
                ex_compact = _compact(ex_norm)

                matched = None
                for path, orig, tokens, compact_tokens in norm_fragments:
                    fulltext = " ".join(tokens)

                    # Skip checks for the first value in characteristics lists
                    # e.g. meta.characteristics[0].values[0] — treat as non-informative
                    if re.match(r"meta\.characteristics\[\d+\]\.values\[0\]", path):
                        continue

                    # ---- >>> NEW ESIM CONTEXT RULE <<< ----
                    if "esim" in ex_norm:
                        if should_skip_esim_case(fulltext):
                            continue  # do NOT exclude — physical SIM present

                    # 1) Exact token match
                    if len(ex_tokens) == 1 and ex_tokens[0] in tokens:
                        matched = (path, orig, ex_tokens[0])
                        break

                    # 2) Compact equality
                    if ex_compact and any(ex_compact == ct for ct in compact_tokens):
                        matched = (path, orig, ex_compact)
                        break

                    # 3) Multiword exact match
                    if len(ex_tokens) > 1:
                        for i in range(len(tokens) - len(ex_tokens) + 1):
                            if tokens[i:i+len(ex_tokens)] == ex_tokens:
                                matched = (path, orig, " ".join(ex_tokens))
                                break
                        if matched:
                            break

                if matched:
                    path, orig, preview_token = matched
                    has_exclusion = True
                    break

            if not has_exclusion:
                filtered.append(product)

        return filtered

    def extract_product_info(self, product: Dict, user_discount: int = 0) -> Optional[Dict]:
        """Извлекает и форматирует информацию о товаре.
            NOTE: не применяет site-base discount здесь — base_price вычисляется в основном цикле
        на основе текущей глобальной настройки.
            на основе текущей глобальной настройкой (`site_base_discount`).
        """
        try:
            product_id = product.get('id')
            name = product.get('name', 'Unknown')
            
            # Цена может быть в разных местах
            price = None
            if 'sizes' in product and len(product['sizes']) > 0:
                size = product['sizes'][0]
                if 'price' in size and 'product' in size['price']:
                    price = size['price']['product'] / 100  # Цена в рублях
            
            if not price or price <= 0:
                logger.warning(f"Invalid price for product {product_id}: {price}")
                return None
            
            seller_name = product.get('supplier', product.get('supplierName', 'Unknown'))
            stock = product.get('totalQuantity', 0)

            url = self._construct_product_url(product_id, name)

            return {
                'product_id': product_id,
                'name': name,
                'price': int(round(price)),   # original price in full rubles (no site discount)
                'seller': seller_name,
                'stock': stock,
                'url': url
            }
        
        except Exception as e:
            logger.error(f"Error extracting product info: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _construct_product_url(product_id: int, product_name: str) -> str:
        """Конструирует прямую ссылку на товар"""
        if not product_id:
            return "https://www.wildberries.ru"
        
        name_slug = product_name[:50].lower().replace(' ', '-')
        return f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx?name={name_slug}"
import asyncio
from datetime import datetime
import logging
from aiogram import Dispatcher, Bot, F
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

import config
import re
from parser.cookies_manager import CookiesManager
from parser.queue_worker import ParserQueueWorker
from middlewares.auth import AuthMiddleware
from handlers import admin, parser, profile

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

db_manager = None
cookies_manager = None
parser_worker = None

if not config.BOT_TOKEN:
    logger.error("BOT_TOKEN не найден в окружении")
    raise SystemExit(1)

storage = MemoryStorage()
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=storage)

async def init_app():
    """Инициализация приложения"""
    global db_manager, cookies_manager, parser_worker, bot, dp
    
    logger.info(f"DEBUG: ADMIN_IDS = {config.ADMIN_IDS}")
    
    from database.manager import DatabaseManager
    db_manager = DatabaseManager()
    await db_manager.init()
    logger.info("Database initialized")
    
    cookies_manager = CookiesManager()
    await cookies_manager.update_cookies()
    logger.info("Cookies manager initialized")
    
    parser_worker = ParserQueueWorker(num_workers=config.PARSER_WORKERS)
    logger.info(f"Parser worker initialized with {config.PARSER_WORKERS} workers")
    
    try:
        import parser.signals as signals
        signals.parse_event = asyncio.Event()
        signals.parser_restart_event = asyncio.Event()
    except Exception:
        logger.warning("Could not initialize parser signals")

    asyncio.create_task(parser_monitoring_loop())
    logger.info("Parser monitoring loop started")
    
    dp.message.middleware(AuthMiddleware(db_manager))
    dp.callback_query.middleware(AuthMiddleware(db_manager))
    
    dp.include_router(admin.router)
    dp.include_router(parser.router)
    dp.include_router(profile.router)
    
    await register_commands(bot)
    
    logger.info("Bot initialized successfully")

async def register_commands(bot: Bot):
    """Регистрация команд бота"""
    commands = [
        BotCommand(command="start", description="🚀 Главное меню"),
        BotCommand(command="parser", description="🔍 Меню парсера"),
        BotCommand(command="profile", description="👤 Профиль"),
        BotCommand(command="admin", description="🔧 Панель администратора"),
        BotCommand(command="status", description="📊 Статус парсинга"),
    ]
    await bot.set_my_commands(commands)

@dp.message(Command("status"))
async def parser_status(message: Message, db_manager):
    """Проверить статус парсинга"""
    user = await db_manager.get_user(message.from_user.id)
    
    if not user or not user.has_access:
        await message.answer("❌ У вас нет доступа")
        return
    
    # Report global parsing configuration and user info
    global_products = await db_manager.get_global_products()
    channel_id = await db_manager.get_setting('notification_channel_id', 'не установлен')

    text = f"""📊 **Статус парсинга**

👤 Пользователь: `{user.telegram_id}`

📦 **Глобальных товаров для парсинга:** {len(global_products)}
📢 Канал уведомлений: {channel_id}

⏱️ **Парсер работает в фоне**
"""

    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("start"))
async def start_command(message: Message, db_manager):
    """Команда /start"""
    user = await db_manager.get_or_create_user(
        message.from_user.id,
        message.from_user.username
    )
    
    logger.info(f"User {message.from_user.id}: is_admin={user.is_admin}, has_access={user.has_access}")
    
    text = """🎯 **WB Parser Bot**

Отслеживание цен на товары в Wildberries с отправкой уведомлений в канал.
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Парсер", callback_data="parser_menu")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile_menu")],
    ])
    
    if user.is_admin:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="🔧 Администратор", callback_data="admin_menu")]
        )
    
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery, db_manager):
    """Главное меню"""
    user = await db_manager.get_user(callback.from_user.id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Парсер", callback_data="parser_menu")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile_menu")],
    ])
    
    if user and user.is_admin:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="🔧 Администратор", callback_data="admin_menu")]
        )
    
    await callback.message.edit_text("🎯 **Главное меню**", reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "parser_menu")
async def parser_menu_callback(callback: CallbackQuery, db_manager):
    """Меню парсера через callback"""
    user = await db_manager.get_user(callback.from_user.id)
    
    if not user or not user.has_access:
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Массовое добавление", callback_data="parser_bulk_add")],
        [InlineKeyboardButton(text="✏️ Массовое редактирование", callback_data="parser_bulk_edit")],
        [InlineKeyboardButton(text="📊 Экспорт по товару", callback_data="parser_export")],
        [InlineKeyboardButton(text="📋 Мои товары", callback_data="parser_my_products")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])
    
    await callback.message.edit_text("🔍 **Меню парсера**", reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "profile_menu")
async def profile_menu_callback(callback: CallbackQuery, db_manager):
    """Профиль через callback"""
    user = await db_manager.get_user(callback.from_user.id)
    
    status = "✅ Активный" if user.has_access else "❌ Нет доступа"
    admin_badge = "👑 АДМИН\n" if user.is_admin else ""
    
    text = f"""👤 **Ваш профиль**

ID: `{user.telegram_id}`
Имя: {user.username or "Не указано"}
Статус: {status}
{admin_badge}Дата регистрации: {user.created_at.strftime('%d.%m.%Y')}
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_menu")
async def admin_menu_callback(callback: CallbackQuery, db_manager):
    """Админ панель через callback"""
    user = await db_manager.get_user(callback.from_user.id)
    
    if not user or not user.is_admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
        # Показываем полную админ-панель (как в /admin команде)
    site_discount = await db_manager.get_setting('site_base_discount', '11')
    channel_id = await db_manager.get_setting('notification_channel_id', 'не установлен')

    text = f"""🔧 **Панель администратора**

⚙️ Настройки парсера

💰 Глобальная скидка: {site_discount}%
📢 ID канала уведомлений: {channel_id}
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Обновить цены", callback_data="admin_update_prices")],
        [InlineKeyboardButton(text="🧹 Очистить товары", callback_data="admin_clear_tables")],
        [InlineKeyboardButton(text="🔄 Перезапустить парсер", callback_data="admin_restart_parser")],
        [InlineKeyboardButton(text="💰 Изменить скидку", callback_data="admin_set_site_discount")],
        [InlineKeyboardButton(text="📢 Установить ID канала", callback_data="admin_set_channel_id")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

async def parser_monitoring_loop():
    """Фоновый цикл мониторинга парсера и отправки уведомлений в канал"""
    import json
    from parser.scraper import WildberriesScraper
    import time
    
    logger.info("Parser monitoring loop started")
    scraper = WildberriesScraper(cookies_manager)
    
    last_cleanup = time.time()

    while True:
        try:
            try:
                import parser.signals as signals
                ev = getattr(signals, 'parse_event', None)
                restart_ev = getattr(signals, 'parser_restart_event', None)
                
                if restart_ev is not None:
                    try:
                        await asyncio.wait_for(restart_ev.wait(), timeout=1)
                        restart_ev.clear()
                        logger.info("Parser restart signal received, reinitializing scraper...")
                        scraper = WildberriesScraper(cookies_manager)
                        continue
                    except asyncio.TimeoutError:
                        pass
                
                if ev is not None:
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=1)
                        ev.clear()
                    except asyncio.TimeoutError:
                        pass
            except Exception:
                pass

            try:
                global_products = await db_manager.get_global_products()

                if not global_products:
                    logger.debug("No global products configured for parsing")
                else:
                    PARSE_LIMIT = 200

                    # Build mapping of name -> rows for all global products, preserving order
                    name_to_rows = {}
                    ordered_names = []
                    for p in global_products:
                        key = (p.name or '').strip()
                        if not key:
                            continue
                        if key not in name_to_rows:
                            ordered_names.append(key)
                            name_to_rows[key] = []
                        name_to_rows[key].append(p)

                    # Select up to PARSE_LIMIT distinct added product names, include all rows for each
                    names_to_process = ordered_names[:PARSE_LIMIT]
                    queries_map = {name: name_to_rows[name] for name in names_to_process}

                    for query, product_rows in queries_map.items():
                        # If an admin requested restart while processing, stop current batch
                        try:
                            import parser.signals as _signals_check
                            _restart_ev = getattr(_signals_check, 'parser_restart_event', None)
                        except Exception:
                            _restart_ev = None
                        if _restart_ev is not None and _restart_ev.is_set():
                            try:
                                _restart_ev.clear()
                            except Exception:
                                pass
                            logger.info("Parser restart requested — aborting current batch to reload products")
                            # Recreate scraper instance so next outer loop iteration uses fresh state
                            scraper = WildberriesScraper(cookies_manager)
                            break
                        try:
                            first_product = product_rows[0]
                            keywords = json.loads(first_product.keywords) if first_product.keywords else []
                            exclusions = json.loads(first_product.exclusions) if first_product.exclusions else []

                            found_products = await scraper.search_product(
                                query=query,
                                keywords=keywords,
                                exclusions=exclusions
                            )

                            for found_raw in found_products:
                                base_info = scraper.extract_product_info(found_raw, user_discount=0)
                                if not base_info:
                                    continue

                                # Determine site-wide discount
                                try:
                                    site_disc_val = await db_manager.get_setting('site_base_discount')
                                    site_base_discount = 11
                                    if site_disc_val is not None:
                                        sd = str(site_disc_val).strip().rstrip('%').strip()
                                        sd_clean = re.sub(r'[^0-9]', '', sd)
                                        if sd_clean:
                                            site_base_discount = int(sd_clean)
                                except Exception:
                                    site_base_discount = 11

                                try:
                                    price_val = float(base_info.get('price') or 0)
                                except Exception:
                                    price_val = 0.0

                                base_price_val = int(round(price_val * (1 - float(site_base_discount) / 100.0)))

                                for prod in product_rows:
                                    # --- NEW: Check if found product model matches global product model ---
                                    # If global product has "Pro Max" but found product only has "Pro", skip this pair
                                    try:
                                        found_name = base_info.get('name', '').lower()
                                        global_name = (prod.name or '').lower()

                                        # Use component extraction to get canonical model strings
                                        try:
                                            global_model = admin._extract_components(prod.name).get('model')
                                        except Exception:
                                            global_model = None
                                        try:
                                            found_model = admin._extract_components(base_info.get('name') or found_raw.get('name') or '').get('model')
                                        except Exception:
                                            found_model = None

                                        # If global product specifies a model (e.g. '17 Pro Max' or '17 Pro'),
                                        # require that the found product contains that model (case-insensitive).
                                        # This blocks less-specific matches: e.g. global='17 Pro Max', found='17 Pro' -> skip.
                                        if global_model:
                                            gml = str(global_model).lower()
                                            f_present = (gml in found_name) or (found_model and gml in str(found_model).lower())
                                            if not f_present:
                                                logger.debug(
                                                    f"Skipping found '{base_info.get('name')}' for global '{prod.name}': "
                                                    f"model mismatch (need '{global_model}')"
                                                )
                                                continue
                                    except Exception as e:
                                        logger.warning(f"Error checking model match: {e}")
                                        # On error, don't block — continue with notification
                                    
                                    try:
                                        thr_min = float(prod.threshold_min or 0.0)
                                    except Exception:
                                        thr_min = 0.0
                                    try:
                                        thr_max = float(prod.threshold_max if prod.threshold_max is not None else prod.threshold_min or 0.0)
                                    except Exception:
                                        thr_max = float(prod.threshold_min or 0.0)

                                    if float(base_price_val) >= float(thr_min) and float(base_price_val) <= float(thr_max):
                                        try:
                                            channel_setting = await db_manager.get_setting('notification_channel_id')
                                            if not channel_setting:
                                                logger.debug("No channel ID configured")
                                                continue
                                            channel_id = channel_setting.strip()

                                            # url = base_info.get('url')
                                            # now_ts = time.time()
                                            # # in-memory dedupe: skip if we've sent this url in the last 24 hours
                                            # last_sent = sent_urls.get(url)
                                            # if last_sent and (now_ts - last_sent) < (24 * 3600):
                                            #     logger.debug(f"Already notified recently for {url}")
                                            #     continue
                                            url = base_info.get('url')
                                            # Проверяем запись в БД: если уже отправляли по такому url и цена не изменилась — пропускаем
                                            sent_rec = await db_manager.get_sent_notification(url)
                                            should_notify = False
                                            
                                            if sent_rec:
                                                try:
                                                    prev_price = float(sent_rec.last_price) if sent_rec.last_price is not None else None
                                                except Exception:
                                                    prev_price = None

                                                # Only notify when price decreased compared to last sent price
                                                if prev_price is None:
                                                    # previous price unknown — send notification
                                                    logger.info(f"Previous price unknown for {url}, sending notification")
                                                    should_notify = True
                                                elif float(base_price_val) < float(prev_price):
                                                    logger.info(f"Price decreased for {url}: {prev_price} → {base_price_val}")
                                                    should_notify = True
                                                else:
                                                    # Price didn't decrease — skip notification
                                                    logger.debug(f"Skipping notify for {url}: price not decreased ({base_price_val} >= {prev_price})")
                                                    should_notify = False
                                            else:
                                                # Товар не отправляли раньше — отправляем уведомление
                                                logger.info(f"First notification for {url}")
                                                should_notify = True
                                            
                                            if not should_notify:
                                                continue

                                            text = (
                                                f"{base_info.get('name')} — {base_info.get('stock', 0)} шт.\n\n"
                                                f"Цена: {int(base_price_val)}₽ -- ( {int(float(thr_max)) - int(base_price_val)} ₽)\n"
                                                f"Порог: {int(float(thr_max))}₽ | {prod.name}\n"
                                            )

                                            supplier_id = found_raw.get('supplierId')
                                            if supplier_id:
                                                seller_link = f"https://www.wildberries.ru/seller/{supplier_id}"
                                                text += f"\n🏪 **Продавец:** [{base_info.get('seller','')}]({seller_link})\n"
                                            else:
                                                text += f"\n**Продавец:** {base_info.get('seller','')}\n"

                                            text += f"\nСсылка: {url}"

                                            logger.debug(f"Notifying: url={url} price_orig={price_val} price_after_discount={base_price_val} site_discount={site_base_discount}")
                                            try:
                                                await bot.send_message(chat_id=channel_id, text=text, parse_mode="Markdown")
                                                # Обновляем/создаём запись о том, что мы отправили этот URL с текущей ценой
                                                try:
                                                    await db_manager.upsert_sent_notification(
                                                        url=url,
                                                        price=float(base_price_val),
                                                        product_name=base_info.get('name'),
                                                        channel_id=channel_id
                                                    )
                                                except Exception as up_err:
                                                    logger.error(f"Failed to upsert sent notification for {url}: {up_err}")
                                                await asyncio.sleep(1.1)
                                            except Exception as send_err:
                                                logger.error(f"Failed to send: {send_err}")

                                        except Exception as e:
                                            logger.error(f"Channel notification error: {e}")

                        except Exception as e:
                            logger.error(f"Query error '{query}': {e}")

                        import random
                        wait = random.uniform(max(1, config.MIN_DELAY), max(config.MIN_DELAY + 1, config.MAX_DELAY))
                        await asyncio.sleep(wait)

            except Exception as e:
                logger.error(f"Parsing loop error: {e}")
            
            # Периодическая очистка старых уведомлений (раз в день)
            now = time.time()
            if now - last_cleanup >= (24 * 3600):
                try:
                    count = await db_manager.cleanup_old_notifications(days=config.PRODUCT_CLEANUP_DAYS)
                    if count > 0:
                        logger.info(f"Cleaned up {count} old channel notifications")
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")
                last_cleanup = now
            
            await asyncio.sleep(5)
        
        except Exception as e:
            logger.error(f"Parser loop exception: {e}")
            await asyncio.sleep(60)

async def main():
    """Главная функция"""
    print("Starting parser bot...")
    await init_app()
    
    try:
        logger.info("Starting bot polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await cleanup_app()

async def cleanup_app():
    """Очистка ресурсов"""
    if db_manager:
        await db_manager.close()
    if bot:
        await bot.session.close()
    logger.info("App cleaned up")

if __name__ == "__main__":
    asyncio.run(main())
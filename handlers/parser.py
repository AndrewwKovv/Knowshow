from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile
import os
import re
import asyncio
import json
import tempfile
from parser.cookies_manager import CookiesManager
from parser.scraper import WildberriesScraper
from parser.export import export_found_products_to_excel, cleanup_export_file

router = Router()

class ParserStates(StatesGroup):
    waiting_for_export_query = State()
    waiting_for_bulk_edit_upload = State()
    waiting_for_price_input = State()

@router.message(Command("parser"))
async def parser_menu(message: Message, db_manager):
    """Меню парсера"""
    user = await db_manager.get_user(message.from_user.id)
    
    if not user or not user.has_access:
        await message.answer("❌ У вас нет доступа к парсеру")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Массовое добавление", callback_data="parser_bulk_add")],
        [InlineKeyboardButton(text="✏️ Массовое редактирование", callback_data="parser_bulk_edit")],
        [InlineKeyboardButton(text="📊 Экспорт по товару", callback_data="parser_export")],
        [InlineKeyboardButton(text="✍️ Редактировать цену", callback_data="parser_edit_price")], 
        [InlineKeyboardButton(text="📋 Мои товары", callback_data="parser_my_products")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])
    
    await message.answer("🔍 **Меню парсера**", reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "parser_bulk_add")
async def bulk_add_info(callback: CallbackQuery):
    """Информация о массовом добавлении"""
    text = """📥 **Массовое добавление товаров**

Отправьте Excel файл со следующими столбцами:
1. **Название** - название товара
2. **Пороговая цена** - минимальная цена для уведомления
3. **Слова исключения** - слова через запятую (например: подделка, брак)
4. **Ключевые слова** - доп. параметры через запятую (nano-SIM, 256GB и т.д.)

Пример:
| iPhone 15 Pro | 50000 | подделка,брак | nano-SIM |
| iPad | 30000 | | 256GB |
"""
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "parser_my_products")
async def show_my_products(callback: CallbackQuery, db_manager):
    """Показать глобальные товары ( read-only )"""
    products = await db_manager.get_global_products()

    if not products:
        await callback.message.edit_text("📋 Глобальные товары ещё не добавлены")
        await callback.answer()
        return

    # Split into pages if message exceeds 4096 chars (Telegram limit)
    max_length = 4000
    pages = []
    current_page = "📋 **Глобальные товары:**\n\n"
    
    for idx, product in enumerate(products, 1):
        try:
            thr_min = product.threshold_min
            thr_max = product.threshold_max
        except Exception:
            thr_min = None
            thr_max = None

        if thr_min is not None and thr_max is not None:
            thr_display = f"{int(thr_min)}-{int(thr_max)}"
        elif thr_max is not None:
            thr_display = f"{int(thr_max)}"
        elif thr_min is not None:
            thr_display = f"{int(thr_min)}"
        else:
            thr_display = "(не задан)"

        # Compact format: single line per product
        item = f"{idx}. {product.name[:50]} – `{thr_display}` руб.\n"
        
        if len(current_page) + len(item) > max_length:
            # Page full, start new page
            if current_page != "📋 **Глобальные товары:**\n\n":
                pages.append(current_page)
            current_page = f"📋 **Глобальные товары (продолжение):**\n\n{item}"
        else:
            current_page += item
    
    # Add final page
    if current_page != "📋 **Глобальные товары:**\n\n":
        pages.append(current_page)
    
    # Send first page or edit existing message
    if pages:
        await callback.message.edit_text(pages[0], parse_mode="Markdown")
        
        # Send additional pages as separate messages
        for page in pages[1:]:
            await callback.message.answer(page, parse_mode="Markdown")
    
    await callback.answer()

@router.callback_query(F.data == "parser_export")
async def export_menu(callback: CallbackQuery, db_manager):
    """Меню экспорта — показываем доступные глобальные товары как кнопки"""
    products = await db_manager.get_global_products()

    if not products:
        await callback.message.edit_text(
            "📊 **Экспорт по товару**\n\nНет доступных товаров для экспорта"
        )
        await callback.answer()
        return

    # Создаём кнопки для каждого товара
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"📦 {p.name[:30]}",
            callback_data=f"export_product_{p.id}"
        )]
        for p in products
    ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="parser_menu")]])

    await callback.message.edit_text(
        "📊 **Экспорт по товару**\n\nВыберите товар для парсинга:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("export_product_"))
async def start_product_export(callback: CallbackQuery, db_manager):
    """Начать парсинг выбранного товара (в фоне)"""
    try:
        product_id = int(callback.data.split("_")[-1])
    except Exception:
        await callback.answer("❌ Ошибка обработки выбора", show_alert=True)
        return

    # Получаем информацию о товаре
    async with db_manager.async_session() as session:
        from database.models import GlobalProduct
        from sqlalchemy import select
        stmt = select(GlobalProduct).where(GlobalProduct.id == product_id)
        result = await session.execute(stmt)
        product = result.scalars().first()

    if not product:
        await callback.answer("❌ Товар не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"⏳ **Парсинг товара:** {product.name}\n\n"
        f"Подождите, идёт поиск результатов..."
    )
    await callback.answer()

    # Запускаем асинхронный парсинг в фоне
    asyncio.create_task(
        export_product_async(
            callback.message,
            db_manager,
            product,
            callback.from_user.id
        )
    )

async def export_product_async(message, db_manager, product, user_id):
    """Асинхронный парсинг товара и создание Excel файла — берёт top-10 результатов"""
    try:
        # Создаём свой cookies manager и скрапер для параллельного парсинга
        cookies_mgr = CookiesManager()
        await cookies_mgr.update_cookies()
        scraper = WildberriesScraper(cookies_mgr)

        # Загружаем keywords/exclusions из записи
        keywords = json.loads(product.keywords) if product.keywords else []
        exclusions = json.loads(product.exclusions) if product.exclusions else []

        # Выполняем поиск — search_product агрегирует основной и keyword-запросы
        found_products = await scraper.search_product(
            query=product.name,
            keywords=keywords,
            exclusions=exclusions
        )

        if not found_products:
            await message.edit_text(
                f"❌ **Экспорт:** {product.name}\n\nТовары не найдены"
            )
            return

        # Берём первые 10 товаров
        top_products = found_products[:10]

        # Получаем глобальную скидку для применения в экспорте
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

        # Генерируем Excel файл
        filepath = await export_found_products_to_excel(
            product.name,
            top_products,
            scraper,
            site_base_discount=site_base_discount
        )

        # Отправляем файл пользователю (в чат, откуда пришёл запрос)
        file = FSInputFile(filepath, filename=f"export_{product.name}.xlsx")
        await message.edit_text(f"✅ **Экспорт завершён:** {product.name}")
        await message.answer_document(
            file,
            caption=f"📊 **Результаты парсинга:** {product.name}\n\n✅ Найдено товаров: {len(top_products)}\n📥 Топ-10 позиций"
        )

        # Удаляем временный файл
        cleanup_export_file(filepath)

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error exporting product {product.name}: {e}", exc_info=True)
        await message.edit_text(f"❌ **Ошибка экспорта:** {product.name}\n\n{str(e)}")

@router.callback_query(F.data == "parser_edit_price")
async def edit_price_list(callback: CallbackQuery, db_manager):
    """Показать список всех глобальных товаров для выбора редактирования цены"""
    products = await db_manager.get_global_products()

    if not products:
        await callback.message.edit_text("📋 Глобальные товары ещё не добавлены")
        await callback.answer()
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✏️ {p.name[:40]}",
            callback_data=f"edit_price_{p.id}"
        )] for p in products
    ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="parser_menu")]])

    await callback.message.edit_text(
        "✍️ **Редактирование цены**\n\nВыберите товар для обновления диапазона цен:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("edit_price_"))
async def start_price_edit(callback: CallbackQuery, state: FSMContext, db_manager):
    """Начало редактирования: выбираем товар и бот ждёт от пользователя текст с новой ценой"""
    try:
        product_id = int(callback.data.split("_")[-1])
    except Exception:
        await callback.answer("❌ Ошибка обработки выбора", show_alert=True)
        return

    async with db_manager.async_session() as session:
        from database.models import GlobalProduct
        from sqlalchemy import select
        stmt = select(GlobalProduct).where(GlobalProduct.id == product_id)
        result = await session.execute(stmt)
        product = result.scalars().first()

    if not product:
        await callback.answer("❌ Товар не найден", show_alert=True)
        return

    # Сохраняем id товара в состоянии и просим ввести новую цену/диапазон
    await state.update_data(edit_product_id=product_id)
    await state.set_state(ParserStates.waiting_for_price_input)

    await callback.message.edit_text(
        f"✍️ **Редактирование цены:**\n\n{product.name}\n\n"
        "Введите новый диапазон цен в формате:\n"
        "• `50000-60000` — min-max\n"
        "• `60000` — одно число (будет использовано как верхняя граница, нижняя = верх - 18000)\n\n"
        "Примеры: `54000-70000` или `134000`",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(ParserStates.waiting_for_price_input)
async def handle_price_input(message: Message, state: FSMContext, db_manager):
    """Обработка ввода новой ценовой границы для выбранного товара и обновление БД"""
    user = await db_manager.get_user(message.from_user.id)
    if not user or not user.is_admin:
        await message.answer("❌ Нет доступа (требуются права администратора)")
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("❌ Пустое сообщение. Введите диапазон цен, например: 50000-60000")
        return

    # Получаем id товара из состояния
    data = await state.get_data()
    product_id = data.get("edit_product_id")
    if not product_id:
        await message.answer("❌ Не удалось определить редактируемый товар. Повторите операцию.")
        await state.clear()
        return

    # Парсим диапазон цены
    def _parse_range(s: str):
        s = s.replace('₽', '').replace(' ', '').strip()
        if '-' in s:
            parts = s.split('-', 1)
            try:
                a = int(re.sub(r'[^0-9]', '', parts[0]))
                b = int(re.sub(r'[^0-9]', '', parts[1]))
                return min(a, b), max(a, b)
            except Exception:
                return None
        else:
            try:
                v = int(re.sub(r'[^0-9]', '', s))
                return max(0, v-18000), v
            except Exception:
                return None

    parsed = _parse_range(text)
    if not parsed:
        await message.answer("❌ Неверный формат. Используйте `min-max` или одно число, например `60000`.", parse_mode="Markdown")
        return

    thr_min, thr_max = parsed

    # Обновляем запись в БД по id
    async with db_manager.async_session() as session:
        from database.models import GlobalProduct
        from sqlalchemy import select
        stmt = select(GlobalProduct).where(GlobalProduct.id == product_id)
        result = await session.execute(stmt)
        gp = result.scalars().first()
        if not gp:
            await message.answer("❌ Товар не найден в БД.")
            await state.clear()
            return

        try:
            gp.threshold_min = float(thr_min)
            gp.threshold_max = float(thr_max)
            from datetime import datetime
            gp.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(gp)
        except Exception as e:
            await message.answer(f"❌ Ошибка обновления: {e}")
            await state.clear()
            return

    await message.answer(f"✅ Диапазон для товара **{gp.name}** обновлён: `{int(gp.threshold_min)}-{int(gp.threshold_max)}`", parse_mode="Markdown")

    # Попробуем сигнализировать парсеру запустить цикл
    try:
        import parser.signals as signals
        ev = getattr(signals, 'parse_event', None)
        if ev is not None:
            ev.set()
    except Exception:
        pass

    await state.clear()

@router.message(ParserStates.waiting_for_export_query)
async def export_product(message: Message, state: FSMContext, db_manager):
    """Экспортировать товар в Excel"""
    from parser.export import export_products_to_excel, cleanup_export_file
    
    search_query = message.text.strip()
    
    try:
        # Отправляем сообщение о начале экспорта
        status_msg = await message.answer("⏳ Подготовка файла экспорта...")
        
        # Генерируем Excel файл
        filepath = await export_products_to_excel(
            message.from_user.id,
            search_query,
            db_manager
        )
        
        # Отправляем файл
        file = FSInputFile(filepath, filename=f"products_{search_query}.xlsx")
        await message.answer_document(
            file,
            caption=f"📊 **Экспорт товаров:** {search_query}\n\n✅ Файл готов к скачиванию"
        )
        
        # Удаляем временный файл после отправки
        cleanup_export_file(filepath)
        
        # Удаляем сообщение статуса
        await status_msg.delete()
        
    except ValueError as e:
        await message.answer(f"⚠️ {str(e)}")
    except Exception as e:
        await message.answer(f"❌ Ошибка при экспорте: {str(e)}")
    
    await state.clear()


@router.callback_query(F.data == "parser_bulk_edit")
async def bulk_edit_callback(callback: CallbackQuery, state: FSMContext, db_manager):
    """Отправить пользователю их текущие товары в Excel для редактирования"""
    from parser.export import export_user_products_to_excel, cleanup_export_file

    await callback.message.edit_text("📥 **Массовое редактирование** — формирую файл с вашими товарами. Отредактируйте и отправьте файл обратно.", parse_mode="Markdown")
    await callback.answer()

    try:
        status = await callback.message.answer("⏳ Подготовка файла для редактирования...")
        filepath = await export_user_products_to_excel(callback.from_user.id, db_manager)
        file = FSInputFile(filepath, filename=f"my_products_{callback.from_user.id}.xlsx")
        await callback.message.answer_document(file, caption="📥 Отредактируйте файл и отправьте его обратно в этот чат")
        cleanup_export_file(filepath)
        await status.delete()

        # Устанавливаем состояние ожидания загрузки файла редактирования
        await state.set_state(ParserStates.waiting_for_bulk_edit_upload)

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при подготовке файла: {e}")
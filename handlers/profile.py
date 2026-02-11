from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

router = Router()

@router.message(Command("profile"))
async def profile_menu(message: Message, db_manager):
    """Профиль пользователя"""
    user = await db_manager.get_user(message.from_user.id)
    
    if not user:
        await message.answer("❌ Вы не зарегистрированы")
        return
    
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
    
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
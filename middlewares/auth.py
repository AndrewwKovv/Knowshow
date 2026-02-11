from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Any, Awaitable
import logging

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseMiddleware):
    def __init__(self, db_manager):
        self.db_manager = db_manager
    
    async def __call__(
        self,
        handler: Callable[[Any], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        username = None
        
        if isinstance(event, Message):
            user_id = event.from_user.id
            username = event.from_user.username
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            username = event.from_user.username
        
        if user_id:
            # Сначала получаем или создаем пользователя
            user = await self.db_manager.get_or_create_user(user_id, username)
            # Затем получаем свежую версию из БД
            fresh_user = await self.db_manager.get_user(user_id)
            
            data["db_manager"] = self.db_manager
            data["current_user"] = fresh_user if fresh_user else user
        
        return await handler(event, data)
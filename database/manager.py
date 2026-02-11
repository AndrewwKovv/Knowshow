from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, delete, text
from datetime import datetime
from config import DATABASE_URL
from .models import Base, User, GlobalProduct, Setting, ChannelNotification
import logging

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.engine = None
        self.async_session = None

    async def init(self):
        """Initialize DB and create tables for current models."""
        self.engine = create_async_engine(DATABASE_URL, echo=False)
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.engine.begin() as conn:
            # Create missing tables if necessary
            await conn.run_sync(Base.metadata.create_all)

            # Ensure the new column `last_seen_at` exists in `channel_notifications`.
            # If it's missing (older DB), add it via ALTER TABLE.
            try:
                await conn.execute(text("SELECT last_seen_at FROM channel_notifications LIMIT 1"))
            except Exception:
                try:
                    await conn.execute(text("ALTER TABLE channel_notifications ADD COLUMN last_seen_at DATETIME"))
                    logger.info("Database migrated: added column channel_notifications.last_seen_at")
                except Exception as e:
                    logger.warning(f"Failed to add last_seen_at column automatically: {e}")

    async def close(self):
        if self.engine:
            await self.engine.dispose()

    # ------------------ User Operations ------------------
    async def get_or_create_user(self, telegram_id, username=None):
        from config import ADMIN_IDS
        import logging
        logger = logging.getLogger(__name__)

        async with self.async_session() as session:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await session.execute(stmt)
            user = result.scalars().first()

            if not user:
                is_admin = telegram_id in ADMIN_IDS
                logger.info(f"Creating user {telegram_id}: is_admin={is_admin}")
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    is_admin=is_admin,
                    has_access=is_admin,
                )
                session.add(user)
                await session.commit()
            else:
                await session.refresh(user)

            return user

    async def get_user(self, telegram_id):
        async with self.async_session() as session:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await session.execute(stmt)
            user = result.scalars().first()
            if user:
                await session.refresh(user)
            return user

    async def grant_access(self, telegram_id):
        async with self.async_session() as session:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await session.execute(stmt)
            user = result.scalars().first()
            if user:
                user.has_access = True
                user.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(user)
                return True
            return False

    async def revoke_access(self, telegram_id):
        async with self.async_session() as session:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await session.execute(stmt)
            user = result.scalars().first()
            if user:
                user.has_access = False
                await session.commit()
                return True
            return False

    # ------------------ Global products ------------------
    async def add_global_product(self, name, threshold_min: float = 0.0, threshold_max: float = None, keywords=None, exclusions=None):
        async with self.async_session() as session:
            stmt = select(GlobalProduct).where(GlobalProduct.name == name)
            result = await session.execute(stmt)
            gp = result.scalars().first()
            if gp:
                gp.threshold_min = threshold_min
                gp.threshold_max = threshold_max if threshold_max is not None else threshold_min
                gp.keywords = keywords
                gp.exclusions = exclusions
                gp.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(gp)
                return gp

            gp = GlobalProduct(
                name=name,
                threshold_min=threshold_min,
                threshold_max=threshold_max if threshold_max is not None else threshold_min,
                keywords=keywords,
                exclusions=exclusions,
            )
            session.add(gp)
            await session.commit()
            await session.refresh(gp)
            return gp

    async def get_global_products(self):
        async with self.async_session() as session:
            stmt = select(GlobalProduct)
            result = await session.execute(stmt)
            return result.scalars().all()

    async def delete_all_global_products(self):
        async with self.async_session() as session:
            stmt = delete(GlobalProduct)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    # ------------------ Settings ------------------
    async def get_setting(self, key: str, default: str = None):
        async with self.async_session() as session:
            stmt = select(Setting).where(Setting.key == key)
            result = await session.execute(stmt)
            s = result.scalars().first()
            if s:
                return s.value
            return default

    async def set_setting(self, key: str, value: str):
        async with self.async_session() as session:
            stmt = select(Setting).where(Setting.key == key)
            result = await session.execute(stmt)
            s = result.scalars().first()
            if s:
                s.value = str(value)
                s.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(s)
                return s
            s = Setting(key=str(key), value=str(value))
            session.add(s)
            await session.commit()
            await session.refresh(s)
            return s

# ------------------ Channel notifications ------------------
    async def get_sent_notification(self, url: str):
        """Возвращает запись ChannelNotification по URL или None"""
        async with self.async_session() as session:
            stmt = select(ChannelNotification).where(ChannelNotification.url == str(url))
            result = await session.execute(stmt)
            rec = result.scalars().first()
            if rec:
                await session.refresh(rec)
            return rec

    async def upsert_sent_notification(self, url: str, price: float, product_name: str = None, channel_id: str = None):
        """
        Создаёт или обновляет запись ChannelNotification.
        `price` — цена после применения site_base_discount (float).
        Note: this no longer updates `last_seen_at` — that field is retained for
        historical purposes but not modified here.
        """
        async with self.async_session() as session:
            stmt = select(ChannelNotification).where(ChannelNotification.url == str(url))
            result = await session.execute(stmt)
            rec = result.scalars().first()
            now = datetime.utcnow()
            if rec:
                rec.last_price = float(price) if price is not None else rec.last_price
                rec.last_sent_at = now
                # Do NOT update `last_seen_at` here — keep historical value unchanged
                if product_name:
                    rec.product_name = product_name
                if channel_id:
                    rec.channel_id = channel_id
                await session.commit()
                await session.refresh(rec)
                logger.debug(f"Updated ChannelNotification for {url}: price={rec.last_price}")
                return rec

            rec = ChannelNotification(
                url=str(url),
                product_name=product_name,
                last_price=float(price) if price is not None else None,
                last_sent_at=now,
                # Do not set last_seen_at on create; keep it NULL to indicate no explicit "seen" timestamp
                last_seen_at=None,
                channel_id=channel_id
            )
            session.add(rec)
            await session.commit()
            await session.refresh(rec)
            logger.debug(f"Created ChannelNotification for {url}: price={rec.last_price}, last_seen_at={rec.last_seen_at}")
            return rec

    async def cleanup_old_notifications(self, days: int = 14):
        """Удаляет ChannelNotification старше N дней"""
        from datetime import timedelta
        async with self.async_session() as session:
            cutoff = datetime.utcnow() - timedelta(days=days)
            stmt = delete(ChannelNotification).where(ChannelNotification.last_sent_at < cutoff)
            result = await session.execute(stmt)
            await session.commit()
            count = result.rowcount
            logger.info(f"Cleaned up {count} old channel notifications (older than {days} days)")
            return count
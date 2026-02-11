from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(Integer, primary_key=True)
    username = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    has_access = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.telegram_id} - {self.username}>"


class Setting(Base):
    """Глобальные настройки (скидка, ID канала и т.д.)"""
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"


class GlobalProduct(Base):
    """Товары для глобального парсинга (управляются админами)"""
    __tablename__ = "global_products"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    threshold_min = Column(Float, nullable=True, default=0.0)
    threshold_max = Column(Float, nullable=True)
    keywords = Column(String, nullable=True)
    exclusions = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<GlobalProduct {self.name} min={self.threshold_min} max={self.threshold_max}>"
    
class ChannelNotification(Base):
    """Хранит информацию о том, какие URL уже отправлялись в канал и по какой цене"""
    __tablename__ = "channel_notifications"

    id = Column(Integer, primary_key=True)
    url = Column(String, nullable=False, unique=True, index=True)
    product_name = Column(String, nullable=True)
    last_price = Column(Float, nullable=True)          # цена при последней отправке (после site_discount)
    last_sent_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)  # когда последний раз видели товар
    channel_id = Column(String, nullable=True)

    def __repr__(self):
        return f"<ChannelNotification url={self.url} price={self.last_price} seen_at={self.last_seen_at}>"
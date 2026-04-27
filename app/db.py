from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from .settings import get_settings
settings = get_settings()
engine = create_async_engine(
    settings.database_url, 
    future=True, 
    echo=False,
    pool_pre_ping=True,  # Проверяет соединение перед использованием
    pool_size=20,        # Увеличиваем размер пула
    max_overflow=10,     # Максимум временных соединений
    pool_recycle=3600,   # Переподключение каждый час (против timeout)
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()


async def get_db() -> AsyncSession:
    """Dependency для получения сессии БД"""
    async with SessionLocal() as session:
        yield session

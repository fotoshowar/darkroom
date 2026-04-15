"""
Database connection for Darkroom service.
Comparte la DB con FotoShow.
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

# Database URL (misma que FotoShow)
DATABASE_URL = "postgresql+asyncpg://postgres:vLFNxunq9A06AJD8@db.gfriigyzsbfugahlriut.supabase.co:5432/postgres"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

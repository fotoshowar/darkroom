"""
Configuration for Darkroom service.
Comparte los mismos settings que FotoShow para JWT.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # JWT (debe coincidir con FotoShow)
    secret_key: str = "changeme"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 días (como FotoShow)

    # Database (misma que FotoShow)
    database_url: str = "postgresql+asyncpg://postgres:vLFNxunq9A06AJD8@db.gfriigyzsbfugahlriut.supabase.co:5432/postgres"

    # FotoShow API (para referencias cruzadas)
    fotoshow_api_url: str = "http://127.0.0.1:8000"

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()

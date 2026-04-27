from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import List, Optional

class Settings(BaseSettings):
    # Core
    database_url: str
    secret_key: str = "devtoken"  # Для JWT токенов
    sign_secret: str = "devsign"  # Для подписи
    
    # Legacy поддержка старого названия
    instr_token: Optional[str] = None

    # CORS (backward-compat: prefer allowed_origins if provided, else cors_origins)
    cors_origins: str = "*"
    allowed_origins: Optional[str] = None

    # Environment name (dev/test/prod)
    environment: str = "development"
    
    # Безопасность
    access_token_expire_minutes: int = 480   # 8 часов
    max_login_attempts: int = 5
    block_duration_minutes: int = 15
    
    # Файлы
    max_file_size_mb: int = 100  # Максимальный размер файла в МБ

    # Базовый URL для генерации ссылок (QR-коды и т.д.)
    # Задаётся через BASE_URL в .env (например: https://your-domain.com)
    base_url: str = "https://localhost"
    
    # Мониторинг
    sentry_dsn: Optional[str] = None  # Sentry DSN для отслеживания ошибок
    
    # AI вопросы
    openai_api_key: Optional[str] = None  # OpenAI API ключ для генерации вопросов
    ai_questions_enabled: bool = True  # Включить/выключить AI вопросы
    ai_questions_pass_threshold: int = 75  # Минимальный процент для прохождения (75%)
    ai_questions_count: int = 4  # Количество вопросов по умолчанию

    # Allow unknown env keys instead of failing fast (robust to extra vars in .env or unit files)
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @property
    def cors_list(self) -> List[str]:
        value = self.allowed_origins if (self.allowed_origins and self.allowed_origins.strip()) else self.cors_origins
        if not value or value == "*":
            return ["*"]
        return [x.strip() for x in value.split(",") if x.strip()]

@lru_cache
def get_settings() -> Settings:
    return Settings()

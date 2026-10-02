"""
Конфигурация приложения
"""
import logging
import sys
from pathlib import Path
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# === Настройка логирования ===
def setup_logging():
    """Настройка логирования для всего приложения"""
    log_format = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Основной логгер
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Уменьшаем логи от сторонних библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("WDM").setLevel(logging.WARNING)
    
    return logging.getLogger("competitor_monitor")

# Инициализация логгера
logger = setup_logging()


class Settings(BaseSettings):
    """Foundation v2 и отдельные настройки совместимости v1."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8",
        extra="ignore", populate_by_name=True,
    )

    app_env: str = "development"
    app_host: str = Field("127.0.0.1", validation_alias=AliasChoices("APP_HOST", "API_HOST"))
    app_port: int = Field(8000, ge=1, le=65535, validation_alias=AliasChoices("APP_PORT", "API_PORT"))
    ai_provider: str = "proxyapi"
    ai_api_key: SecretStr = SecretStr("")
    ai_base_url: str = "https://api.proxyapi.ru/v1"
    ai_model: str = "openai/gpt-6-luna"
    ai_reasoning_effort: str = "low"
    ai_timeout_seconds: float = Field(60.0, gt=0)

    database_url: str = "sqlite:///./data/app.db"
    upload_dir: Path = PROJECT_ROOT / "data/uploads"
    screenshot_dir: Path = PROJECT_ROOT / "data/screenshots"
    max_image_mb: int = Field(10, gt=0)
    max_pdf_mb: int = Field(25, gt=0)
    max_text_chars: int = Field(30000, gt=0)
    max_web_text_chars: int = Field(25000, gt=0)
    max_pdf_pages_analyzed: int = Field(8, gt=0)
    browser_headless: bool = True
    browser_timeout_ms: int = Field(20000, gt=0)
    browser_viewport_width: int = Field(1440, gt=0)
    browser_viewport_height: int = Field(1200, gt=0)
    cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    log_level: str = "INFO"

    # Legacy v1: не смешиваем старый URL/model ID с настройками v2.
    proxy_api_key: SecretStr = SecretStr("")
    proxy_api_base_url: str = "https://api.proxyapi.ru/openai/v1"
    openai_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o-mini"
    
    # История
    history_file: Path = PROJECT_ROOT / "history.json"
    max_history_items: int = 10
    
    # Парсер
    parser_timeout: int = 10
    parser_user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    
    @field_validator("upload_dir", "screenshot_dir", "history_file", mode="after")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return (PROJECT_ROOT / value).resolve()

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Unsupported LOG_LEVEL")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ai_configured(self) -> bool:
        """Наличие v2 credentials, не проверка доступности провайдера."""
        return bool(self.ai_api_key.get_secret_value().strip())

    @property
    def api_host(self) -> str:
        return self.app_host

    @property
    def api_port(self) -> int:
        return self.app_port


settings = Settings()


from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OUTREACHER_")

    database_url: str = "postgresql+asyncpg://outreacher:outreacher@localhost:5432/outreacher"
    base_url: str = "https://outreach.example.ru"
    app_secret: str = "dev-secret-change-me"
    b24_client_id: str = ""
    b24_client_secret: str = ""
    b24_webhook_secret: str = ""
    operator_password: str = "operator"
    tz_offset_hours: int = 3  # Мск
    send_hour_from: int = 8
    send_hour_to: int = 12  # эксклюзивно
    daily_cap_default: int = 100
    legal_entity: str = 'ООО «Пример»'
    legal_address: str = "г. Москва, ул. Примерная, 1"
    legal_phone: str = "+7 (495) 000-00-00"


@lru_cache
def get_settings() -> Settings:
    return Settings()

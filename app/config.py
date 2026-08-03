from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://resale_radar:resale_radar@localhost:5432/resale_radar"
    redis_url: str = "redis://localhost:6379/0"
    discord_bot_token: str = ""


settings = Settings()

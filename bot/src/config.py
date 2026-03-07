from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    discord_bot_token: str
    discord_server_id: int

    discord_channel_alerts_high: int
    discord_channel_alerts_all: int
    discord_channel_daily_brief: int
    discord_channel_regime_shifts: int
    discord_channel_onchain: int
    discord_channel_bot_commands: int
    discord_channel_system_health: int

    # Individual connection components (resolved from .env)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "cryptomacro"
    postgres_user: str = "cryptomacro"
    postgres_password: str = "cryptomacro_dev_password"
    redis_host: str = "localhost"
    redis_port: int = 6379
    nats_url: str = "nats://localhost:4222"

    # Constructed DSNs (built from components above)
    db_dsn: str = ""
    redis_url: str = ""

    @model_validator(mode="after")
    def build_dsns(self) -> "BotSettings":
        if not self.db_dsn:
            self.db_dsn = (
                f"postgresql://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        if not self.redis_url:
            self.redis_url = f"redis://{self.redis_host}:{self.redis_port}/0"
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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

    # Service connections
    db_dsn: str = "postgresql://postgres:postgres@localhost:5432/cryptomacro"
    redis_url: str = "redis://localhost:6379"
    nats_url: str = "nats://localhost:4222"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

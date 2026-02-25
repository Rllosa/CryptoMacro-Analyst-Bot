from pydantic_settings import BaseSettings


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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

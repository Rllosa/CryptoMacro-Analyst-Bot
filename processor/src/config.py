from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Runtime configuration for the processor service.

    All fields are loaded from environment variables (or a .env file).
    Aliases match the env var names used in docker-compose and .env.example.
    """

    # NATS
    nats_url: str = Field(default="nats://nats:4222", alias="NATS_URL")
    nats_subject: str = Field(default="market.candles.>", alias="NATS_SUBJECT")
    nats_stream: str = Field(default="MARKET", alias="NATS_STREAM")
    nats_consumer_name: str = Field(default="normalizer", alias="NATS_CONSUMER_NAME")

    # TimescaleDB
    postgres_host: str = Field(default="timescaledb", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="cryptomacro", alias="POSTGRES_DB")
    postgres_user: str = Field(default="cryptomacro", alias="POSTGRES_USER")
    postgres_password: str = Field(default="cryptomacro_dev_password", alias="POSTGRES_PASSWORD")

    # Batch settings
    batch_size: int = Field(default=100, alias="BATCH_SIZE")
    batch_timeout_secs: float = Field(default=5.0, alias="BATCH_TIMEOUT_SECS")

    # Backfill
    gap_threshold_minutes: int = Field(default=5, alias="GAP_THRESHOLD_MINUTES")

    # Binance REST (for gap backfill)
    binance_rest_base: str = Field(default="https://fapi.binance.com", alias="BINANCE_REST_BASE")

    # Redis (for feature caching)
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    # Feature engine
    thresholds_path: str = Field(default="/app/configs/thresholds.yaml", alias="THRESHOLDS_PATH")
    symbols_path: str = Field(default="/app/configs/symbols.yaml", alias="SYMBOLS_PATH")
    feature_interval_secs: int = Field(default=300, alias="FEATURE_INTERVAL_SECS")

    # Coinglass (DI-5)
    coinglass_api_key: str = Field(default="", alias="COINGLASS_API_KEY")
    coinglass_base_url: str = Field(
        default="https://open-api-v4.coinglass.com/api",
        alias="COINGLASS_BASE_URL",
    )
    coinglass_poll_interval_secs: int = Field(default=300, alias="COINGLASS_POLL_INTERVAL_SECS")
    yahoo_poll_interval_secs: int = Field(default=300, alias="YAHOO_POLL_INTERVAL_SECS")

    # Deribit (DI-6)
    deribit_poll_interval_secs: int = Field(default=3600, alias="DERIBIT_POLL_INTERVAL_SECS")  # 1-hour DVOL resolution

    # CoinGecko (DI-7)
    coingecko_poll_interval_secs: int = Field(default=600, alias="COINGECKO_POLL_INTERVAL_SECS")  # 10-minute BTC.D polling

    @property
    def db_dsn(self) -> str:
        """Build a libpq-style DSN string for psycopg/AsyncConnectionPool."""
        return (
            f"host={self.postgres_host} "
            f"port={self.postgres_port} "
            f"dbname={self.postgres_db} "
            f"user={self.postgres_user} "
            f"password={self.postgres_password}"
        )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
    }

from pydantic_settings import BaseSettings


class ApiSettings(BaseSettings):
    db_dsn: str = "postgresql://postgres:postgres@localhost:5432/cryptomacro"
    redis_url: str = "redis://localhost:6379"
    environment: str = "development"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

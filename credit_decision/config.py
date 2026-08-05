"""Central configuration — env-driven, pydantic-validated."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- PostgreSQL ---------------------------------------------------
    postgres_user: str = "volt"
    postgres_password: str = "volt"
    postgres_db: str = "volt_credit"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # ---- MLflow / API -------------------------------------------------
    mlflow_tracking_uri: str = "http://localhost:5000"
    api_url: str = "http://localhost:8000"

    # ---- Data generation ----------------------------------------------
    data_seed: int = 42
    data_n_applications: int = 150_000
    data_n_txns_per_client_min: int = 3
    data_n_txns_per_client_max: int = 220

    # ---- Business / model ---------------------------------------------
    model_approval_threshold: float = 0.35
    model_cost_fp: float = 1.0   # cost of approving a defaulter
    model_cost_fn: float = 0.2   # cost of declining a good client

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

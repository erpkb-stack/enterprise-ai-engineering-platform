"""Centralised, typed configuration loaded from environment variables (12-factor).

Every tunable lives here so that business code never reads os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIECC_", env_file=".env", extra="ignore")

    # --- runtime -----------------------------------------------------------------------------
    env: Literal["local", "test", "staging", "production"] = "local"
    app_name: str = "AI Engineering Command Center"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5180", "http://127.0.0.1:5180"])

    # --- persistence -------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://aiecc:aiecc@localhost:5442/aiecc"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_s: float = 10.0
    db_echo: bool = False
    redis_url: str | None = "redis://localhost:6390/0"

    # --- security ----------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me-0123456789")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "aiecc"
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 60 * 60 * 24 * 7
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt integration credentials at rest.
    encryption_key: SecretStr = SecretStr("ZGV2LW9ubHktZW5jcnlwdGlvbi1rZXktMzJieXRlcyE=")
    oidc_issuer_url: str | None = None  # integration-ready: see docs/security/authentication.md
    oidc_client_id: str | None = None
    rate_limit_per_minute: int = 600
    rate_limit_auth_per_minute: int = 20

    # --- AI providers ------------------------------------------------------------------------
    default_provider: str = "mock"
    default_model: str = "mock-reasoner-1"
    embedding_provider: str = "mock"  # mock | ollama | openai
    embedding_model: str = "hash-embed-768"
    embedding_dim: int = 768
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    nvidia_nim_api_key: SecretStr | None = None
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_timeout_s: float = 60.0

    # --- execution limits (bounded autonomy) -------------------------------------------------
    max_agent_steps_hard_limit: int = 25
    max_concurrent_executions: int = 16
    max_queued_executions: int = 64
    agent_total_timeout_s: float = 180.0
    tool_timeout_s: float = 30.0
    workflow_max_nodes: int = 100

    # --- integrations ------------------------------------------------------------------------
    http_tool_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["api.github.com", "httpbin.org"])
    upload_max_bytes: int = 15 * 1024 * 1024

    # --- jobs & observability ----------------------------------------------------------------
    jobs_mode: Literal["inline", "arq"] = "inline"
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "aiecc-backend"

    # --- feature flags -----------------------------------------------------------------------
    feature_flags: dict[str, bool] = Field(
        default_factory=lambda: {
            "llm_judge": True,
            "workflow_python_functions": True,
            "experimental_context_compression": True,
            "red_team_lab": True,
            "load_test_runner": True,
        }
    )

    seed_demo_data: bool = True
    # Demo-only credential for seeded users (documented in README). Never used outside local/test.
    demo_password: SecretStr = SecretStr("Aiecc!Demo2026")

    @field_validator("cors_origins", "http_tool_allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str) and not v.startswith("["):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def flag(self, name: str) -> bool:
        return bool(self.feature_flags.get(name, False))


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and "dev-only" in s.jwt_secret.get_secret_value():
        raise RuntimeError("Refusing to start in production with the development JWT secret")
    return s

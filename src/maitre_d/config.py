"""Settings read from the environment and `.env`.

Secrets are `SecretStr` so they never appear in a repr, a traceback or a log line by accident;
the raw value is only reachable through an explicit `.get_secret_value()` call at the point of use.

`TOOL_BEARER_SECRET` is optional because Alembic and `scripts/seed.py` need only the database URL.
The auth boundary fails closed when it is unset or blank: tool requests get a 500, never an
authenticated response.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+asyncpg://maitre_d:maitre_d@localhost:5432/maitre_d"
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://maitre_d:maitre_d@localhost:5432/maitre_d_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    # The test suite's database. It lives here so `.env` is the single source for it, rather than
    # the tests reading `os.environ` while pydantic-settings reads `.env`.
    test_database_url: str = DEFAULT_TEST_DATABASE_URL
    tool_bearer_secret: SecretStr | None = None
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_webhook_secret: SecretStr | None = None
    elevenlabs_agent_id: str | None = None
    # Which ElevenLabs environment a web session resolves `backend_host` in. Unset means the
    # platform's own default, production, which is the only environment until something is deployed.
    elevenlabs_environment: str | None = None
    # Where the built dashboard is. Unset means `frontend/dist`, which is served only if it exists.
    frontend_dist: str | None = None
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

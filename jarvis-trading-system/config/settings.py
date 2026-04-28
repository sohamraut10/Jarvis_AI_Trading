"""
Settings — loaded in priority order:
  1. data/settings.json   (Control Room writes here; highest priority)
  2. Environment variables / .env   (fallback)
  3. Built-in defaults

This means the Control Room is the single source of truth: set everything
there, no environment variables required.
"""
import json
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

SETTINGS_FILE = Path("data/settings.json")


class _JsonFileSource(PydanticBaseSettingsSource):
    """Reads data/settings.json and injects it as the highest-priority source."""

    def get_field_value(self, field, field_name: str):
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not SETTINGS_FILE.exists():
            return {}
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # Dhan credentials (optional — set via Control Room)
    dhan_client_id:    str = Field("", alias="DHAN_CLIENT_ID")
    dhan_access_token: str = Field("", alias="DHAN_ACCESS_TOKEN")

    # Trading mode
    paper_mode: bool = Field(True, alias="PAPER_MODE")

    # Capital & risk
    initial_capital:   float = Field(10_000.0, alias="INITIAL_CAPITAL")
    kill_switch_pct:   float = Field(0.03, alias="KILL_SWITCH_PCT")
    kelly_fraction:    float = Field(0.5,  alias="KELLY_FRACTION")

    # Regime / strategy
    hmm_states:               int = Field(4,   alias="HMM_STATES")
    regime_lookback_bars:     int = Field(200, alias="REGIME_LOOKBACK_BARS")
    sharpe_rank_window_days:  int = Field(20,  alias="SHARPE_RANK_WINDOW_DAYS")

    # WebSocket server
    ws_host: str = Field("0.0.0.0", alias="WS_HOST")
    ws_port: int = Field(8765,       alias="WS_PORT")

    # Logging / persistence
    log_level:        str = Field("INFO",               alias="LOG_LEVEL")
    intent_log_path:  str = Field("logs/intent.jsonl",  alias="INTENT_LOG_PATH")
    pnl_db_path:      str = Field("data/pnl.db",        alias="PNL_DB_PATH")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # JSON file wins over env vars wins over .env wins over defaults
        return (
            init_settings,
            _JsonFileSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @property
    def kill_switch_amount(self) -> float:
        return self.initial_capital * self.kill_switch_pct


def load_settings() -> Settings:
    """Always re-read from disk so the server can apply saved changes."""
    return Settings()


settings = load_settings()

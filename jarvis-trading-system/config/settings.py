from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Dhan
    dhan_client_id: str = Field(..., alias="DHAN_CLIENT_ID")
    dhan_access_token: str = Field(..., alias="DHAN_ACCESS_TOKEN")

    # Trading mode
    paper_mode: bool = Field(True, alias="PAPER_MODE")

    # Capital & risk
    initial_capital: float = Field(10_000.0, alias="INITIAL_CAPITAL")
    kill_switch_pct: float = Field(0.03, alias="KILL_SWITCH_PCT")
    kelly_fraction: float = Field(0.5, alias="KELLY_FRACTION")

    # Regime / strategy
    hmm_states: int = Field(4, alias="HMM_STATES")
    regime_lookback_bars: int = Field(200, alias="REGIME_LOOKBACK_BARS")
    sharpe_rank_window_days: int = Field(20, alias="SHARPE_RANK_WINDOW_DAYS")

    # WebSocket server
    ws_host: str = Field("0.0.0.0", alias="WS_HOST")
    ws_port: int = Field(8000, alias="WS_PORT")

    # Logging
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    intent_log_path: str = Field("logs/intent.jsonl", alias="INTENT_LOG_PATH")
    pnl_db_path: str = Field("data/pnl.db", alias="PNL_DB_PATH")

    @property
    def kill_switch_amount(self) -> float:
        return self.initial_capital * self.kill_switch_pct


settings = Settings()

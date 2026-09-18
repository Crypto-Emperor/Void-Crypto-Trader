from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).parent

# Load root .env then keys/api_keys.env (keys folder wins on conflicts)
load_dotenv(BASE_DIR / ".env")
_keys_env = BASE_DIR / "keys" / "api_keys.env"
if _keys_env.exists():
    load_dotenv(_keys_env, override=True)

# Optional single-line key files in keys/
def _read_key_file(name: str) -> str | None:
    path = BASE_DIR / "keys" / name
    if not path.exists():
        return None
    try:
        line = path.read_text(encoding="utf-8").strip().splitlines()
        for raw in line:
            s = raw.strip()
            if s and not s.startswith("#"):
                return s
    except Exception:
        return None
    return None

STATE_FILE = BASE_DIR / "portfolio_state.json"
PID_FILE = BASE_DIR / "autotrader.pid"
STOP_FLAG = BASE_DIR / "STOP"
LOG_FILE = BASE_DIR / "autotrader.log"


class Settings(BaseModel):
    starting_balance: float = float(os.getenv("STARTING_BALANCE", "10000"))
    live_max_usd: float = float(os.getenv("LIVE_MAX_USD", "500"))

    mode: str = os.getenv("MODE", "sim").lower()
    price_source: str = os.getenv("PRICE_SOURCE", "auto").lower()

    fomo_access_token: str | None = os.getenv("FOMO_ACCESS_TOKEN") or None
    fomo_wallet: str | None = os.getenv("FOMO_WALLET_ADDRESS") or None
    fomo_email: str | None = os.getenv("FOMO_EMAIL") or None
    fomo_password: str | None = os.getenv("FOMO_PASSWORD") or None

    strategy: str = os.getenv("STRATEGY", "momentum").lower()
    check_interval_sec: int = int(os.getenv("CHECK_INTERVAL_SEC", "1"))
    max_position_pct: float = float(os.getenv("MAX_POSITION_PCT", "6"))
    trade_usd: float = float(os.getenv("TRADE_USD", "100"))
    symbols: str = os.getenv("SYMBOLS", "sol,eth,btc,bonk,wif")

    expected_annual_return: float = float(os.getenv("EXPECTED_ANNUAL_RETURN", "0.25"))
    volatility: float = float(os.getenv("VOLATILITY", "0.60"))

    # Honeypot / security
    honeypot_check: bool = os.getenv("HONEYPOT_CHECK", "true").lower() in ("1", "true", "yes")
    honeypot_strict: bool = os.getenv("HONEYPOT_STRICT", "true").lower() in ("1", "true", "yes")
    goplus_api_key: str | None = os.getenv("GOPLUS_API_KEY") or _read_key_file("goplus.key")
    default_chain: str = os.getenv("DEFAULT_CHAIN", "solana").lower()

    # FOMO leaderboard
    leaderboard_window: str = os.getenv("LEADERBOARD_WINDOW", "24h").lower()
    leaderboard_top_n: int = int(os.getenv("LEADERBOARD_TOP_N", "10"))
    leaderboard_min_mentions: int = int(os.getenv("LEADERBOARD_MIN_MENTIONS", "2"))
    fomoapi_key: str | None = os.getenv("FOMOAPI_KEY") or os.getenv("FOMO_API_KEY") or _read_key_file("fomoapi.key")

    @property
    def is_live(self) -> bool:
        return self.mode == "live" and (
            bool(self.fomo_access_token) or bool(self.fomo_email)
        )

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().lower() for s in self.symbols.split(",") if s.strip()]


settings = Settings()

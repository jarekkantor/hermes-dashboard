import os
from pathlib import Path
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


DASHBOARD_SECRET: str = os.getenv("DASHBOARD_SECRET", "")
HERMES_HOME: Path = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
DASHBOARD_DB: Path = Path(
    os.getenv("DASHBOARD_DB", str(Path(__file__).resolve().parent.parent / "dashboard.db"))
)
STATE_DB: Path = HERMES_HOME / "state.db"

ALERT_DAILY_USD: float = float(os.getenv("ALERT_DAILY_USD", "25"))
ALERT_WEEKLY_USD: float = float(os.getenv("ALERT_WEEKLY_USD", "100"))
ALERT_MONTHLY_USD: float = float(os.getenv("ALERT_MONTHLY_USD", "300"))

SESSION_TTL_DAYS: int = int(os.getenv("SESSION_TTL_DAYS", "7"))

ANTHROPIC_API_BASE = "https://api.anthropic.com"

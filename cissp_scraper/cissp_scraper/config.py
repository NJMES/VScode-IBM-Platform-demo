import os
from pathlib import Path
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

_base = Path(__file__).parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(_base / "data" / "questions.db")))
EXPORTS_DIR = Path(os.getenv("EXPORTS_DIR", str(_base / "exports")))
BROWSER_CONTEXT_DIR = _base / "data" / "browser_context"

ADAM_GORDON_URL = "https://www.linkedin.com/in/adam-gordon-cissp/recent-activity/shares/"

CISSP_DOMAINS = (
    "Security & Risk Management",
    "Asset Security",
    "Security Architecture & Engineering",
    "Network & Communications Security",
    "Identity & Access Management",
    "Security Assessment & Testing",
    "Security Operations",
    "Software Development Security",
)

REVIEW_STATUS_PENDING = "pending_review"
REVIEW_STATUS_VALIDATED = "validated"
REVIEW_STATUS_REJECTED = "rejected"


class MissingAPIKeyError(RuntimeError):
    pass


def require_anthropic_key() -> str:
    if not ANTHROPIC_API_KEY:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set.\n\n"
            "To get one:\n"
            "  1. Visit https://console.anthropic.com/\n"
            "  2. Go to API Keys and create a new key\n"
            "  3. Add it to cissp_scraper/.env as:\n"
            "     ANTHROPIC_API_KEY=sk-ant-...\n"
        )
    return ANTHROPIC_API_KEY


def require_linkedin_creds() -> tuple[str, str]:
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        raise RuntimeError(
            "LINKEDIN_EMAIL and LINKEDIN_PASSWORD must be set in cissp_scraper/.env"
        )
    return LINKEDIN_EMAIL, LINKEDIN_PASSWORD

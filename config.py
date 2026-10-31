import os

try:
    from dotenv import load_dotenv
except ImportError:  # Systemd supplies environment variables in production.
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


def _parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USERS = _parse_int_list(os.getenv("ADMIN_USERS", ""))
ADMIN_MESSAGE = int(os.getenv("ADMIN_MESSAGE", "0") or 0)
TECHNICIAN_USERS = _parse_int_list(
    os.getenv("TECHNICIAN_USERS", str(ADMIN_MESSAGE) if ADMIN_MESSAGE else "")
)
BOT_KEY = os.getenv("BOT_KEY", "kalisto").strip() or "kalisto"
DATABASE_BACKEND = os.getenv("DATABASE_BACKEND", "sqlite").strip().lower()
TICKETING_DB_HOST = os.getenv("TICKETING_DB_HOST", "127.0.0.1").strip()
TICKETING_DB_PORT = int(os.getenv("TICKETING_DB_PORT", "55432"))
TICKETING_DB_NAME = os.getenv("TICKETING_DB_NAME", "activity_trial").strip()
TICKETING_DB_USER = os.getenv("TICKETING_DB_USER", "ticket_bot").strip()
TICKETING_DB_PASSWORD = os.getenv("TICKETING_DB_PASSWORD", "")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
DESTINATION_MODEL = os.getenv(
    "DESTINATION_MODEL",
    "~openai/gpt-sol-latest",
).strip()
DESTINATION_PROMPT_PATH = os.getenv(
    "DESTINATION_PROMPT_PATH",
    "content/destination/system_prompt.md",
).strip()

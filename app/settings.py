from dataclasses import dataclass

import config as legacy_config


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_users: frozenset[int]
    admin_message: int
    bot_key: str
    technician_users: frozenset[int] = frozenset()
    openrouter_api_key: str = ""
    destination_model: str = "~openai/gpt-sol-latest"
    destination_prompt_path: str = "content/destination/system_prompt.md"


def load_settings() -> Settings:
    """Собирает типизированные настройки из текущего config.py."""
    if not legacy_config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required; set it in .env or the environment")
    if not legacy_config.ADMIN_USERS:
        raise RuntimeError("ADMIN_USERS is required; use comma-separated Telegram IDs")
    if not legacy_config.ADMIN_MESSAGE:
        raise RuntimeError("ADMIN_MESSAGE is required")
    technician_users = frozenset(
        int(value) for value in legacy_config.TECHNICIAN_USERS
    )
    if not technician_users:
        raise RuntimeError(
            "TECHNICIAN_USERS is required; use comma-separated Telegram IDs"
        )
    allowed_recipients = frozenset(legacy_config.ADMIN_USERS) | technician_users
    if int(legacy_config.ADMIN_MESSAGE) not in allowed_recipients:
        raise RuntimeError(
            "ADMIN_MESSAGE must belong to ADMIN_USERS or TECHNICIAN_USERS"
        )
    return Settings(
        bot_token=legacy_config.BOT_TOKEN,
        admin_users=frozenset(int(value) for value in legacy_config.ADMIN_USERS),
        admin_message=int(legacy_config.ADMIN_MESSAGE),
        bot_key=getattr(legacy_config, "BOT_KEY", "default"),
        technician_users=technician_users,
        openrouter_api_key=legacy_config.OPENROUTER_API_KEY,
        destination_model=legacy_config.DESTINATION_MODEL,
        destination_prompt_path=legacy_config.DESTINATION_PROMPT_PATH,
    )

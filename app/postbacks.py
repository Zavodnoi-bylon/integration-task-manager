from pathlib import Path


POSTBACKS_DIR = Path(__file__).resolve().parent.parent / "content" / "postbacks"

BOT_TENANTS = {
    "kalisto": {
        "traffic_angels": "Traffic Angels",
        "ai_buying_g": "AI Buying - G",
    },
    "mephisto": {
        "the_raw": "TheRaw",
    },
}


def get_tenants(bot_key: str) -> dict[str, str]:
    """Возвращает только тенанты, разрешённые текущему экземпляру бота."""
    return BOT_TENANTS.get(bot_key, {})


def load_template(bot_key: str, tenant_key: str) -> str:
    """Читает статичный шаблон с диска, проверив доступ бота к тенанту."""
    tenants = get_tenants(bot_key)
    if tenant_key not in tenants:
        raise KeyError(f"Tenant {tenant_key!r} is unavailable for bot {bot_key!r}")

    template_path = POSTBACKS_DIR / f"{tenant_key}.txt"
    return template_path.read_text(encoding="utf-8").strip()

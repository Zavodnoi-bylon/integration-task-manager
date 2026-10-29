from html import escape


def html(value: object) -> str:
    return escape(str(value))


def short_text(value: str, limit: int = 32) -> str:
    normalized = " ".join(str(value).split())
    return normalized if len(normalized) <= limit else f"{normalized[:limit - 1]}…"


def split_plain_text(text: str, limit: int = 3900) -> list[str]:
    """Делит длинный plain-text шаблон по абзацам для лимита Telegram."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = paragraph
    if current:
        chunks.append(current)
    return chunks

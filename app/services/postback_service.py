from app import postbacks
from app.settings import Settings


class PostbackService:
    def __init__(self, settings: Settings) -> None:
        self._bot_key = settings.bot_key

    def list_tenants(self) -> dict[str, str]:
        return postbacks.get_tenants(self._bot_key)

    def get_template(self, tenant_key: str) -> str:
        return postbacks.load_template(self._bot_key, tenant_key)

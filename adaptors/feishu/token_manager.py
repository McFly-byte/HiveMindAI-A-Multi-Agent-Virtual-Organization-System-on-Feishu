from config.settings import get_settings
from adaptors.feishu.errors import FeishuNotConfiguredError


class FeishuTokenManager:
    """Manage Feishu tenant access token. Real API integration is TODO."""
    def get_tenant_access_token(self) -> str:
        settings = get_settings()
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise FeishuNotConfiguredError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")
        raise NotImplementedError("TODO: request tenant_access_token from Feishu OpenAPI")

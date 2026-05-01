from pydantic import BaseModel
from config.settings import get_settings


class HealthResponse(BaseModel):
    status: str
    app_name: str
    app_env: str
    feishu_configured: bool
    llm_configured: bool


def get_health() -> HealthResponse:
    """Return lightweight heartbeat information without calling business workflows."""
    settings = get_settings()
    return HealthResponse(status="ok", app_name=settings.app_name, app_env=settings.app_env, feishu_configured=bool(settings.feishu_app_id and settings.feishu_app_secret and settings.feishu_app_token), llm_configured=bool(settings.llm_api_key and settings.llm_model))

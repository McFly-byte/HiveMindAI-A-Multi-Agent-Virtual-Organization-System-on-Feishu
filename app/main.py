from fastapi import FastAPI
from app.logging_config import configure_logging
from config.settings import get_settings
from gateway.routes import router

settings = get_settings()
configure_logging(settings.log_level)
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(router)

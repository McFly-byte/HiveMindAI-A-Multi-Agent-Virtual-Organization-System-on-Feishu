from config.settings import get_settings


def main() -> None:
    """Print safe environment readiness without exposing secrets."""
    settings = get_settings()
    print({"app_env": settings.app_env, "feishu_configured": bool(settings.feishu_app_id and settings.feishu_app_secret and settings.feishu_app_token), "llm_configured": bool(settings.llm_api_key and settings.llm_model), "trace_local_dir": str(settings.trace_local_dir)})


if __name__ == "__main__":
    main()

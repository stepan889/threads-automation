from app.config import Settings


def test_admin_ids_are_parsed() -> None:
    settings = Settings(
        telegram_bot_token="token",
        admin_telegram_ids="10, 20",  # type: ignore[arg-type]
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert settings.admin_telegram_ids == frozenset({10, 20})


def test_agentapi_model_has_no_code_default() -> None:
    settings = Settings(
        telegram_bot_token="token",
        admin_telegram_ids="10",  # type: ignore[arg-type]
        database_url="sqlite+aiosqlite:///:memory:",
        agentapi_model="",
    )
    assert settings.agentapi_model == ""

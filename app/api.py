from html import escape

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionFactory
from app.repository import get_or_create_state
from app.services.crypto import TokenCipher
from app.services.errors import ServiceError
from app.services.oauth_state import OAuthStateCodec
from app.services.threads import ThreadsClient

settings = get_settings()
app = FastAPI(title="Threads Bot API", version="1.0.0")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, str]:
    async with SessionFactory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/oauth/threads/callback", response_class=HTMLResponse, tags=["oauth"])
async def threads_callback(code: str = Query(min_length=1), state: str = Query(min_length=1)) -> HTMLResponse:
    client = ThreadsClient(settings.threads_client_id, settings.threads_client_secret, settings.threads_redirect_uri, settings.threads_api_version)
    try:
        telegram_id = OAuthStateCodec(settings.threads_client_secret, settings.oauth_state_ttl_seconds).decode(state)
        if telegram_id not in settings.admin_telegram_ids:
            raise ServiceError("Этот Telegram ID не имеет доступа к боту.")
        token = await client.exchange_code(code)
        encrypted = TokenCipher(settings.token_encryption_key).encrypt(token.access_token)
        async with SessionFactory() as session:
            bot_state = await get_or_create_state(session, telegram_id, settings.default_post_interval_minutes)
            bot_state.threads_connected = True
            bot_state.threads_user_id = token.user_id
            bot_state.threads_username = token.username
            bot_state.threads_access_token = encrypted
            bot_state.threads_token_expires_at = token.expires_at
            await session.commit()
        return HTMLResponse("<h1>Threads подключён</h1><p>Можно закрыть эту страницу и вернуться в Telegram.</p>")
    except ServiceError as exc:
        return HTMLResponse(f"<h1>Подключение не завершено</h1><p>{escape(str(exc))}</p>", status_code=400)
    finally:
        await client.close()

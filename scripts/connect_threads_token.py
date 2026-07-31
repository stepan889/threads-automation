"""One-time local import of a Threads user token without exposing it in argv."""
import asyncio
from datetime import datetime, timedelta, timezone
from getpass import getpass

import httpx

from app.config import get_settings
from app.db import SessionFactory
from app.repository import get_or_create_state
from app.services.crypto import TokenCipher


async def main() -> None:
    settings = get_settings()
    token = getpass("Вставьте Threads access token (ввод скрыт): ").strip()
    if not token:
        raise SystemExit("Токен не введён.")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"https://graph.threads.net/{settings.threads_api_version}/me",
            params={"fields": "id,username", "access_token": token},
        )
        if response.status_code != 200:
            raise SystemExit(f"Threads отклонил токен (HTTP {response.status_code}).")
        profile = response.json()
    telegram_id = next(iter(settings.admin_telegram_ids))
    async with SessionFactory() as session:
        state = await get_or_create_state(session, telegram_id, settings.default_post_interval_minutes)
        state.threads_connected = True
        state.threads_user_id = str(profile["id"])
        state.threads_username = profile.get("username")
        state.threads_access_token = TokenCipher(settings.token_encryption_key).encrypt(token)
        # Meta's generator creates a long-lived token; store the documented lifetime.
        state.threads_token_expires_at = datetime.now(timezone.utc) + timedelta(days=60)
        await session.commit()
    print(f"Threads подключён: @{profile.get('username', 'unknown')} (токен сохранён зашифрованным).")


if __name__ == "__main__":
    asyncio.run(main())

"""One-time import of a long-lived token from Amvera secrets."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.db import SessionFactory
from app.repository import get_or_create_state
from app.services.crypto import TokenCipher


async def main() -> None:
    settings = get_settings()
    token = os.environ["THREADS_BOOTSTRAP_TOKEN"].strip()
    if not token:
        raise SystemExit("THREADS_BOOTSTRAP_TOKEN is empty")

    telegram_id_text = os.getenv("THREADS_BOOTSTRAP_TELEGRAM_ID", "").strip()
    if telegram_id_text:
        telegram_id = int(telegram_id_text)
        if telegram_id not in settings.admin_telegram_ids:
            raise SystemExit("THREADS_BOOTSTRAP_TELEGRAM_ID is not in ADMIN_TELEGRAM_IDS")
    elif len(settings.admin_telegram_ids) == 1:
        telegram_id = next(iter(settings.admin_telegram_ids))
    else:
        raise SystemExit("Set THREADS_BOOTSTRAP_TELEGRAM_ID when there is more than one admin")

    async with SessionFactory() as session:
        existing = await get_or_create_state(
            session, telegram_id, settings.default_post_interval_minutes
        )
        if (
            existing.threads_connected
            and existing.threads_user_id
            and existing.threads_access_token
        ):
            print("Threads bootstrap skipped: this Telegram user is already connected")
            return

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"https://graph.threads.net/{settings.threads_api_version}/me",
            params={"fields": "id,username", "access_token": token},
        )
        if response.status_code != 200:
            raise SystemExit(f"Threads rejected bootstrap token (HTTP {response.status_code})")
        profile = response.json()

    expires_text = os.getenv("THREADS_BOOTSTRAP_EXPIRES_AT", "").strip()
    if expires_text:
        expires_at = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = datetime.now(timezone.utc) + timedelta(days=60)

    async with SessionFactory() as session:
        state = await get_or_create_state(
            session, telegram_id, settings.default_post_interval_minutes
        )
        state.threads_connected = True
        state.threads_user_id = str(profile["id"])
        state.threads_username = profile.get("username")
        state.threads_access_token = TokenCipher(settings.token_encryption_key).encrypt(token)
        state.threads_token_expires_at = expires_at
        await session.commit()

    print(f"Threads bootstrap completed for @{profile.get('username', 'unknown')}")


if __name__ == "__main__":
    asyncio.run(main())

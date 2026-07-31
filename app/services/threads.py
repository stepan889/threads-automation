from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.services.errors import ServiceError
from app.services.interfaces import PublishResult


@dataclass(frozen=True)
class ThreadsToken:
    access_token: str
    user_id: str
    username: str | None
    expires_at: datetime


class ThreadsClient:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, api_version: str = "v1.0", client: httpx.AsyncClient | None = None) -> None:
        self.client_id, self.client_secret, self.redirect_uri = client_id, client_secret, redirect_uri
        self.base_url = f"https://graph.threads.net/{api_version}"
        self._client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None

    def authorization_url(self, state: str) -> str:
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise ServiceError("Threads OAuth не настроен: проверьте CLIENT_ID, CLIENT_SECRET и REDIRECT_URI.")
        return "https://threads.net/oauth/authorize?" + urlencode({"client_id": self.client_id, "redirect_uri": self.redirect_uri, "scope": "threads_basic,threads_content_publish", "response_type": "code", "state": state})

    async def exchange_code(self, code: str) -> ThreadsToken:
        try:
            short = await self._client.post("https://graph.threads.net/oauth/access_token", data={"client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "authorization_code", "redirect_uri": self.redirect_uri, "code": code})
            short.raise_for_status()
            short_data = short.json()
            long = await self._client.get("https://graph.threads.net/access_token", params={"grant_type": "th_exchange_token", "client_secret": self.client_secret, "access_token": short_data["access_token"]})
            long.raise_for_status()
            data = long.json()
            access_token = data["access_token"]
            profile = await self._client.get(f"{self.base_url}/me", params={"fields": "id,username", "access_token": access_token})
            profile.raise_for_status()
            profile_data = profile.json()
        except httpx.TimeoutException as exc:
            raise ServiceError("Threads OAuth не ответил вовремя.") from exc
        except httpx.HTTPStatusError as exc:
            raise ServiceError(f"Threads OAuth вернул ошибку HTTP {exc.response.status_code}.") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError("Threads OAuth вернул некорректный ответ.") from exc
        return ThreadsToken(access_token, str(profile_data["id"]), profile_data.get("username"), datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 5184000))))

    async def refresh(self, access_token: str) -> tuple[str, datetime]:
        try:
            response = await self._client.get("https://graph.threads.net/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": access_token})
            response.raise_for_status()
            data = response.json()
            return data["access_token"], datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 5184000)))
        except httpx.TimeoutException as exc:
            raise ServiceError("Обновление токена Threads превысило таймаут.") from exc
        except (httpx.HTTPStatusError, KeyError, ValueError, TypeError) as exc:
            raise ServiceError("Не удалось обновить токен Threads; подключите аккаунт заново.") from exc

    async def publish(self, user_id: str, access_token: str, text: str) -> PublishResult:
        if not user_id or not access_token:
            raise ServiceError("Аккаунт Threads не подключён.")
        try:
            container = await self._client.post(f"{self.base_url}/{user_id}/threads", data={"media_type": "TEXT", "text": text, "access_token": access_token})
            container.raise_for_status()
            creation_id = container.json()["id"]
            published = await self._client.post(f"{self.base_url}/{user_id}/threads_publish", data={"creation_id": creation_id, "access_token": access_token})
            published.raise_for_status()
            external_id = str(published.json()["id"])
        except httpx.TimeoutException as exc:
            raise ServiceError("Threads API не ответил вовремя; проверьте историю перед повтором.") from exc
        except httpx.HTTPStatusError as exc:
            raise ServiceError(f"Threads API вернул ошибку HTTP {exc.response.status_code}.") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError("Threads API вернул некорректный ответ.") from exc
        return PublishResult(external_id=external_id)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.errors import ServiceError
from app.services.openrouter import OpenRouterPostGenerator, THREADS_TEXT_LIMIT
from app.services.threads import ThreadsClient


@pytest.mark.asyncio
async def test_openrouter_success() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": " Готовый пост "}}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OpenRouterPostGenerator("key", "model", "system", client=client).generate("topic")
    assert result.text == "Готовый пост"


@pytest.mark.asyncio
async def test_openrouter_truncates_at_word_boundary() -> None:
    content = "слово " * 100
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OpenRouterPostGenerator("key", "model", "system", client=client).generate()
    assert len(result.text) <= THREADS_TEXT_LIMIT
    assert result.text.endswith("слово")


@pytest.mark.asyncio
async def test_openrouter_removes_word_cut_by_token_limit() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "А вы используете ИИ в повседневных зад"}, "finish_reason": "length"}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OpenRouterPostGenerator("key", "model", "system", client=client).generate()
    assert result.text == "А вы используете ИИ в повседневных"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 503])
async def test_openrouter_http_errors_are_safe(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"error": {"message": "secret upstream detail"}}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ServiceError, match=f"HTTP {status}"):
            await OpenRouterPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_openrouter_timeout() -> None:
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ServiceError, match="вовремя"):
            await OpenRouterPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_openrouter_empty_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ServiceError, match="пустой"):
            await OpenRouterPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_threads_publish_two_steps() -> None:
    paths = []
    def handler(request: httpx.Request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"id": "container" if request.url.path.endswith("/threads") else "post-1"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ThreadsClient("id", "secret", "https://example.test/cb", client=client).publish("user", "token", "text")
    assert result.external_id == "post-1"
    assert paths == ["/v1.0/user/threads", "/v1.0/user/threads_publish"]


@pytest.mark.asyncio
async def test_threads_rejects_missing_connection() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(ServiceError, match="не подключён"):
            await ThreadsClient("id", "secret", "uri", client=client).publish("", "", "text")


@pytest.mark.asyncio
async def test_threads_exchange_and_refresh() -> None:
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/access_token": return httpx.Response(200, json={"access_token": "short"})
        if request.url.path == "/access_token": return httpx.Response(200, json={"access_token": "long", "expires_in": 100})
        if request.url.path == "/v1.0/me": return httpx.Response(200, json={"id": "u", "username": "name"})
        return httpx.Response(200, json={"access_token": "new", "expires_in": 200})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        threads = ThreadsClient("id", "secret", "uri", client=client)
        token = await threads.exchange_code("code")
        refreshed, expiry = await threads.refresh("long")
    assert (token.access_token, token.user_id, refreshed) == ("long", "u", "new")
    assert expiry > datetime.now(timezone.utc) + timedelta(seconds=150)

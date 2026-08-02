from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import json
import pytest

from app.services.errors import ServiceError
from app.services.agentapi import AgentApiPostGenerator, THREADS_TEXT_LIMIT
from app.services.threads import ThreadsClient


def test_threads_authorization_url_requests_required_permissions() -> None:
    url = ThreadsClient("id", "secret", "https://example.test/cb").authorization_url("state")
    params = parse_qs(urlparse(url).query)
    assert set(params["scope"][0].split(",")) == {"threads_basic", "threads_content_publish"}
    assert params["state"] == ["state"]


@pytest.mark.asyncio
async def test_agentapi_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.agentapi.ru/v1/ai/chat/completions"
        assert request.headers["Authorization"] == "Bearer key"
        return httpx.Response(200, json={"choices": [{"message": {"content": " Готовый пост "}}]})
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await AgentApiPostGenerator("key", "model", "system", client=client).generate("topic")
    assert result.text == "Готовый пост"


@pytest.mark.asyncio
async def test_agentapi_truncates_at_word_boundary() -> None:
    content = "слово " * 100
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await AgentApiPostGenerator("key", "model", "system", client=client).generate()
    assert len(result.text) <= THREADS_TEXT_LIMIT
    assert result.text.endswith("слово")


@pytest.mark.asyncio
async def test_agentapi_removes_word_cut_by_token_limit() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "А вы используете ИИ в повседневных зад"}, "finish_reason": "length"}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await AgentApiPostGenerator("key", "model", "system", client=client).generate()
    assert result.text == "А вы используете ИИ в повседневных"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 503])
async def test_agentapi_http_errors_are_safe(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"error": {"message": "secret upstream detail"}}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ServiceError, match=f"HTTP {status}"):
            await AgentApiPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_agentapi_http_error_includes_safe_message() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": {"message": "Model slug is invalid"}}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ServiceError, match="HTTP 400: Model slug is invalid"):
            await AgentApiPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_agentapi_timeout() -> None:
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ServiceError, match="вовремя"):
            await AgentApiPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_agentapi_empty_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ServiceError, match="пустой"):
            await AgentApiPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_agentapi_null_content_is_a_safe_service_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ServiceError, match="пустой ответ"):
            await AgentApiPostGenerator("key", "model", "system", client=client).generate()


@pytest.mark.asyncio
async def test_agentapi_retry_options_are_sent() -> None:
    captured = {}
    def handler(request: httpx.Request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Готово"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await AgentApiPostGenerator("key", "model", "system", client=client).generate(
            "short", max_completion_tokens=1600, reasoning_effort="low"
        )
    assert captured["max_tokens"] == 1600
    assert captured["reasoning"] == {"effort": "low", "exclude": True}


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

import httpx

from app.services.errors import EmptyGenerationError, ServiceError
from app.services.interfaces import GeneratedPost

THREADS_TEXT_LIMIT = 500


def _truncate_at_word_boundary(text: str, limit: int) -> str:
    """Fit text into the limit without leaving a partial final word."""
    text = text.strip()
    if len(text) <= limit:
        return text

    candidate = text[: limit + 1]
    if candidate[limit].isspace():
        return candidate[:limit].rstrip()

    boundary = max(candidate.rfind(" ", 0, limit + 1), candidate.rfind("\n", 0, limit + 1), candidate.rfind("\t", 0, limit + 1))
    if boundary < 0:
        raise ServiceError(f"Текст нельзя сократить до {limit} символов без разрыва слова.")
    return candidate[:boundary].rstrip()


def _remove_incomplete_last_word(text: str) -> str:
    """Remove a token that may have been cut off by the model token limit."""
    text = text.rstrip()
    if not text or not text[-1].isalnum():
        return text
    head, separator, _ = text.rpartition(" ")
    return head.rstrip() if separator else text


class AgentApiPostGenerator:
    def __init__(self, api_key: str, model: str, system_prompt: str, timeout: float = 30, client: httpx.AsyncClient | None = None) -> None:
        self.api_key, self.model, self.system_prompt = api_key, model, system_prompt
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def generate(self, prompt: str | None = None, system_prompt: str | None = None,
                       *, max_completion_tokens: int = 600,
                       reasoning_effort: str | None = None) -> GeneratedPost:
        if not self.api_key or not self.model:
            raise ServiceError("AgentAPI не настроен: задайте AGENTAPI_KEY и AGENTAPI_MODEL.")
        messages = [{"role": "system", "content": f"{system_prompt or self.system_prompt}\nОтвет — только готовый завершённый текст, не более {THREADS_TEXT_LIMIT} символов. Обязательно закончи последнее слово и предложение."}]
        messages.append({"role": "user", "content": prompt or "Создай новый пост."})
        payload = {"model": self.model, "messages": messages, "max_completion_tokens": max_completion_tokens}
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
        try:
            response = await self._client.post("https://api.agentapi.ru/v1/ai/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise EmptyGenerationError("AgentAPI вернул пустой ответ без текста поста. Попробуйте ещё раз; если ошибка повторяется, проверьте выбранную модель.")
            text = content.strip()
            finish_reason = data["choices"][0].get("finish_reason")
        except httpx.TimeoutException as exc:
            raise ServiceError("AgentAPI не ответил вовремя. Попробуйте ещё раз.") from exc
        except httpx.HTTPStatusError as exc:
            raise ServiceError(f"AgentAPI вернул ошибку HTTP {exc.response.status_code}.") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ServiceError("AgentAPI вернул некорректный или пустой ответ.") from exc
        if finish_reason == "length":
            text = _remove_incomplete_last_word(text)
        if not text:
            raise EmptyGenerationError("AgentAPI вернул пустой ответ без завершённого текста поста.")
        text = _truncate_at_word_boundary(text, THREADS_TEXT_LIMIT)
        return GeneratedPost(text=text)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import httpx
from httpx_sse import aconnect_sse

from mini_agent.core.types import Message, ModelEvent, ToolSpec
from mini_agent.model_api.sse import OpenAIStreamDecoder, SSEDecodeError


class ModelAPIError(RuntimeError):
    pass


class FixedModelClient:
    """OpenAI-compatible streaming client for the fixed Qwen model."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_id: str,
        max_output_tokens: int = 16_384,
        timeout_seconds: float = 600.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=15.0),
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "FixedModelClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[ModelEvent]:
        payload: dict[str, object] = {
            "model": self._model_id,
            "messages": [message.to_openai() for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self._max_output_tokens,
        }
        if tools:
            payload["tools"] = [tool.to_openai() for tool in tools]
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        decoder = OpenAIStreamDecoder()
        url = f"{self._base_url}/chat/completions"

        try:
            async with aconnect_sse(
                self._client,
                "POST",
                url,
                headers=headers,
                json=payload,
            ) as event_source:
                response = event_source.response
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:2000]
                    body = body.replace(self._api_key, "***")
                    raise ModelAPIError(
                        f"Model API returned HTTP {response.status_code}: {body}"
                    )
                async for sse in event_source.aiter_sse():
                    for event in decoder.feed(sse.data):
                        yield event
                if not decoder.completed:
                    raise ModelAPIError("Model stream ended before the [DONE] marker")
        except ModelAPIError:
            raise
        except (httpx.HTTPError, SSEDecodeError) as exc:
            message = str(exc).replace(self._api_key, "***")
            raise ModelAPIError(message) from exc

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from collections.abc import Awaitable, Callable
import random

import httpx
from httpx_sse import aconnect_sse

from mini_agent.core.types import (
    Message,
    ModelAttemptFailed,
    ModelAttemptStarted,
    ModelEvent,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from mini_agent.model_api.retry import RetryPolicy, parse_retry_after
from mini_agent.model_api.sse import OpenAIStreamDecoder, SSEDecodeError


class ModelAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_kind: str = "model_api",
        status_code: int | None = None,
        retryable: bool = False,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.status_code = status_code
        self.retryable = retryable
        self.request_id = request_id
        self.retry_after = retry_after


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
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
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
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._random_value = random_value

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
        url = f"{self._base_url}/chat/completions"

        visible_output = False
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            yield ModelAttemptStarted(attempt, self._retry_policy.max_attempts)
            try:
                async for event in self._stream_once(url, headers, payload):
                    if isinstance(event, (TextDelta, ReasoningDelta, ToolCall)):
                        visible_output = True
                    yield event
                return
            except ModelAPIError as exc:
                will_retry = (
                    exc.retryable
                    and not visible_output
                    and attempt < self._retry_policy.max_attempts
                )
                delay = None
                if will_retry:
                    delay = self._retry_policy.delay_seconds(
                        attempt,
                        retry_after=exc.retry_after,
                        random_value=self._random_value,
                    )
                yield ModelAttemptFailed(
                    attempt=attempt,
                    error_kind=exc.error_kind,
                    message=self._redact(str(exc)),
                    retryable=exc.retryable,
                    will_retry=will_retry,
                    request_id=exc.request_id,
                    retry_delay_seconds=delay,
                )
                if not will_retry:
                    raise
                await self._sleep(delay or 0.0)

    async def _stream_once(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> AsyncIterator[ModelEvent]:
        decoder = OpenAIStreamDecoder()
        try:
            async with aconnect_sse(
                self._client,
                "POST",
                url,
                headers=headers,
                json=payload,
            ) as event_source:
                response = event_source.response
                request_id = self._request_id(response)
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:2000]
                    raise ModelAPIError(
                        self._redact(
                            f"Model API returned HTTP {response.status_code}: {body}"
                        ),
                        error_kind="http_status",
                        status_code=response.status_code,
                        retryable=response.status_code in {429, 502, 503, 504},
                        request_id=request_id,
                        retry_after=parse_retry_after(response.headers.get("retry-after")),
                    )
                async for sse in event_source.aiter_sse():
                    for event in decoder.feed(sse.data):
                        yield event
                if not decoder.completed:
                    raise ModelAPIError(
                        "Model stream ended before the [DONE] marker",
                        error_kind="incomplete_stream",
                        retryable=True,
                        request_id=request_id,
                    )
        except ModelAPIError:
            raise
        except httpx.TransportError as exc:
            raise ModelAPIError(
                self._redact(str(exc)),
                error_kind="transport",
                retryable=True,
                request_id=self._request_id_from_exception(exc),
            ) from exc
        except SSEDecodeError as exc:
            raise ModelAPIError(
                self._redact(str(exc)),
                error_kind="sse_decode",
                retryable=False,
            ) from exc

    def _redact(self, message: str) -> str:
        return message.replace(self._api_key, "***")

    @staticmethod
    def _request_id(response: httpx.Response) -> str | None:
        for name in ("x-request-id", "request-id", "x-ms-request-id"):
            if value := response.headers.get(name):
                return value
        return None

    @staticmethod
    def _request_id_from_exception(exc: httpx.TransportError) -> str | None:
        response = getattr(exc, "response", None)
        if isinstance(response, httpx.Response):
            return FixedModelClient._request_id(response)
        return None

from __future__ import annotations

import httpx
import pytest

from mini_agent.core.types import (
    Message,
    ModelAttemptFailed,
    ModelAttemptStarted,
    ModelResponseCompleted,
    TextDelta,
    ToolCall,
)
from mini_agent.model_api.client import FixedModelClient, ModelAPIError
from mini_agent.model_api.retry import RetryPolicy
from mini_agent.model_api.sse import OpenAIStreamDecoder


def test_sse_decoder_reassembles_tool_calls_and_usage() -> None:
    decoder = OpenAIStreamDecoder()
    events = []
    events += decoder.feed('{"choices":[{"delta":{"content":"hi","tool_calls":[{"index":0,"id":"c1","function":{"name":"read_","arguments":"{\\"pa"}}]}}]}')
    events += decoder.feed('{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"file","arguments":"th\\":\\"a.txt\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"total_tokens":12}}')
    events += decoder.feed("[DONE]")

    assert TextDelta("hi") in events
    assert ToolCall("c1", "read_file", {"path": "a.txt"}) in events
    completed = next(item for item in events if isinstance(item, ModelResponseCompleted))
    assert completed.usage == {"total_tokens": 12}
    assert decoder.completed


@pytest.mark.asyncio
async def test_model_rejects_incomplete_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = FixedModelClient(
        api_key="test-secret",
        base_url="https://example.invalid/v1",
        model_id="qwen3.7-plus",
        http_client=http,
    )
    with pytest.raises(ModelAPIError, match=r"before the \[DONE\]"):
        [event async for event in model.stream([Message(role="user", content="hello")])]
    await http.aclose()


@pytest.mark.asyncio
async def test_model_redacts_key_from_http_error() -> None:
    key = "not-a-real-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"bad key {key}")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = FixedModelClient(
        api_key=key,
        base_url="https://example.invalid/v1",
        model_id="qwen3.7-plus",
        http_client=http,
    )
    with pytest.raises(ModelAPIError) as caught:
        [event async for event in model.stream([Message(role="user", content="hello")])]
    assert key not in str(caught.value)
    await http.aclose()


@pytest.mark.asyncio
async def test_model_retries_retryable_status_before_visible_output() -> None:
    requests = 0
    slept: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(
                503,
                headers={"retry-after": "2", "x-request-id": "req-503"},
                text="temporarily unavailable",
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"ok"},'
                b'"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            ),
        )

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = FixedModelClient(
        api_key="secret",
        base_url="https://example.invalid/v1",
        model_id="fixed",
        http_client=http,
        retry_policy=RetryPolicy(base_delay_seconds=0.1),
        sleep=fake_sleep,
        random_value=lambda: 0.0,
    )
    events = [
        event async for event in model.stream([Message(role="user", content="hello")])
    ]
    assert requests == 2
    assert slept == [2.0]
    assert [event.attempt for event in events if isinstance(event, ModelAttemptStarted)] == [1, 2]
    failure = next(event for event in events if isinstance(event, ModelAttemptFailed))
    assert failure.will_retry
    assert failure.request_id == "req-503"
    assert TextDelta("ok") in events
    await http.aclose()


@pytest.mark.asyncio
async def test_model_retries_connection_error() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
        )

    async def no_sleep(_: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = FixedModelClient(
        api_key="secret",
        base_url="https://example.invalid/v1",
        model_id="fixed",
        http_client=http,
        sleep=no_sleep,
    )
    events = [
        event async for event in model.stream([Message(role="user", content="hello")])
    ]
    assert requests == 2
    assert TextDelta("ok") in events
    await http.aclose()


@pytest.mark.asyncio
async def test_model_does_not_retry_non_retryable_status() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(401, text="unauthorized")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = FixedModelClient(
        api_key="secret",
        base_url="https://example.invalid/v1",
        model_id="fixed",
        http_client=http,
    )
    with pytest.raises(ModelAPIError):
        [event async for event in model.stream([Message(role="user", content="hello")])]
    assert requests == 1
    await http.aclose()


@pytest.mark.asyncio
async def test_model_never_retries_after_visible_output() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = FixedModelClient(
        api_key="secret",
        base_url="https://example.invalid/v1",
        model_id="fixed",
        http_client=http,
    )
    events = []
    with pytest.raises(ModelAPIError):
        async for event in model.stream([Message(role="user", content="hello")]):
            events.append(event)
    assert requests == 1
    failure = next(event for event in events if isinstance(event, ModelAttemptFailed))
    assert failure.retryable
    assert not failure.will_retry
    await http.aclose()

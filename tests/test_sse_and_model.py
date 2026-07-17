from __future__ import annotations

import httpx
import pytest

from mini_agent.core.types import Message, ModelResponseCompleted, TextDelta, ToolCall
from mini_agent.model_api.client import FixedModelClient, ModelAPIError
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

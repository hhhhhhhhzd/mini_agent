from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from mini_agent.core.contracts import ModelClient
from mini_agent.core.types import Message, ModelResponseCompleted, TextDelta
from mini_agent.context.token_budget import TokenBudget


COMPRESSION_VERSION = "fixed-summary-v1"
SUMMARY_SYSTEM_PROMPT = """Create a compact checkpoint for continuing an agent session.
Use exactly these headings: Goal, Constraints, Confirmed Facts, Completed Work,
Files and Artifacts, Tool Failures, Pending Work, Continuation Context.
Preserve exact paths, identifiers, decisions, errors, pending tasks and user constraints.
Do not invent facts. Do not include secrets. Return only the checkpoint."""


@dataclass(frozen=True)
class CompressionResult:
    summary: str
    through_seq: int
    version: str = COMPRESSION_VERSION


class FixedCompressor:
    def __init__(self, model: ModelClient, budget: TokenBudget) -> None:
        self._model = model
        self._budget = budget

    def select_prefix(
        self,
        messages: Sequence[Message],
        *,
        target_remaining_tokens: int,
    ) -> list[Message]:
        if len(messages) <= 1:
            return []
        # A legal cut is at a turn boundary and never between an assistant tool
        # call and its tool results. Keep at least the newest message.
        pending_ids: set[str] = set()
        legal_cuts: list[int] = []
        for index, message in enumerate(messages[:-1], start=1):
            if message.role == "assistant":
                pending_ids.update(call.id for call in message.tool_calls)
            elif message.role == "tool" and message.tool_call_id:
                pending_ids.discard(message.tool_call_id)
            next_message = messages[index]
            if not pending_ids and next_message.role == "user":
                legal_cuts.append(index)
        if not legal_cuts:
            return []
        for cut in legal_cuts:
            remaining = messages[cut:]
            if self._budget.estimate_messages(remaining) <= target_remaining_tokens:
                return list(messages[:cut])
        return list(messages[: legal_cuts[-1]])

    async def compress(
        self,
        messages: Sequence[Message],
        *,
        existing_summary: str | None = None,
        target_remaining_tokens: int | None = None,
    ) -> CompressionResult | None:
        selected = self.select_prefix(
            messages,
            target_remaining_tokens=(
                target_remaining_tokens
                if target_remaining_tokens is not None
                else self._budget.target_tokens
            ),
        )
        if not selected:
            return None
        serializable = [message.to_openai() for message in selected]
        source = json.dumps(serializable, ensure_ascii=False, indent=2)
        if existing_summary:
            source = f"Previous checkpoint:\n{existing_summary}\n\nNew events:\n{source}"
        request = [
            Message(role="system", content=SUMMARY_SYSTEM_PROMPT),
            Message(role="user", content=source),
        ]
        parts: list[str] = []
        async for event in self._model.stream(request, ()):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, ModelResponseCompleted):
                break
        summary = "".join(parts).strip()
        if not summary:
            raise RuntimeError("Compression model returned an empty summary")
        return CompressionResult(summary=summary, through_seq=len(selected))

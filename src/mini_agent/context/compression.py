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
    input_tokens: int | None = None
    output_tokens: int | None = None
    stage_count: int = 1


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
        new_events = json.dumps(serializable, ensure_ascii=False, indent=2)
        max_input_tokens = max(256, int(self._budget.context_window * 0.5))
        source = new_events
        if existing_summary:
            source = f"Previous checkpoint:\n{existing_summary}\n\nNew events:\n{source}"

        total_input = 0
        total_output = 0
        stage_count = 0
        if self._budget.estimate_text(source) > max_input_tokens:
            stage_summaries: list[str] = []
            for index, chunk in enumerate(
                self._split_source(new_events, max_input_tokens), start=1
            ):
                summary, input_tokens, output_tokens = await self._summarize_source(
                    f"Checkpoint source stage {index}:\n{chunk}"
                )
                stage_summaries.append(summary)
                total_input += input_tokens
                total_output += output_tokens
                stage_count += 1
            source = self._combine_stage_summaries(stage_summaries, existing_summary)
            reduction_round = 0
            while self._budget.estimate_text(source) > max_input_tokens:
                reduction_round += 1
                if reduction_round > 8:
                    raise RuntimeError("Compression stages did not converge")
                reduced: list[str] = []
                chunks = self._split_source(source, max_input_tokens)
                for index, chunk in enumerate(chunks, start=1):
                    summary, input_tokens, output_tokens = await self._summarize_source(
                        f"Checkpoint reduction {reduction_round}.{index}:\n{chunk}"
                    )
                    reduced.append(summary)
                    total_input += input_tokens
                    total_output += output_tokens
                    stage_count += 1
                next_source = self._combine_stage_summaries(reduced, None)
                if len(next_source) >= len(source) and len(chunks) == 1:
                    raise RuntimeError("Compression stage failed to reduce its input")
                source = next_source

        summary, input_tokens, output_tokens = await self._summarize_source(source)
        total_input += input_tokens
        total_output += output_tokens
        stage_count += 1
        return CompressionResult(
            summary=summary,
            through_seq=len(selected),
            input_tokens=total_input,
            output_tokens=total_output,
            stage_count=stage_count,
        )

    async def _summarize_source(self, source: str) -> tuple[str, int, int]:
        request = [
            Message(role="system", content=SUMMARY_SYSTEM_PROMPT),
            Message(role="user", content=source),
        ]
        parts: list[str] = []
        input_tokens = self._budget.estimate_messages(request)
        output_tokens = 0
        async for event in self._model.stream(request, ()):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, ModelResponseCompleted):
                if event.usage:
                    input_tokens = int(
                        event.usage.get(
                            "prompt_tokens", event.usage.get("input_tokens", input_tokens)
                        )
                    )
                    output_tokens = int(
                        event.usage.get(
                            "completion_tokens", event.usage.get("output_tokens", 0)
                        )
                    )
                break
        summary = "".join(parts).strip()
        if not summary:
            raise RuntimeError("Compression model returned an empty summary")
        if output_tokens == 0:
            output_tokens = self._budget.estimate_text(summary)
        return summary, input_tokens, output_tokens

    @staticmethod
    def _combine_stage_summaries(
        summaries: Sequence[str], existing_summary: str | None
    ) -> str:
        parts: list[str] = []
        if existing_summary:
            parts.append(f"Previous checkpoint:\n{existing_summary}")
        parts.extend(
            f"Stage summary {index}:\n{summary}"
            for index, summary in enumerate(summaries, start=1)
        )
        return "\n\n".join(parts)

    @staticmethod
    def _split_source(source: str, max_tokens: int) -> list[str]:
        max_bytes = max(1, max_tokens * 3)
        chunks: list[str] = []
        current: list[str] = []
        current_bytes = 0
        for character in source:
            size = len(character.encode("utf-8"))
            if current and current_bytes + size > max_bytes:
                chunks.append("".join(current))
                current = []
                current_bytes = 0
            current.append(character)
            current_bytes += size
        if current:
            chunks.append("".join(current))
        return chunks

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mini_agent.context.compression import CompressionResult, FixedCompressor
from mini_agent.context.system_rules import SystemRuleBlock, SystemRuleLoader
from mini_agent.context.token_budget import TokenBudget
from mini_agent.core.types import Message, ToolSpec


@dataclass(frozen=True)
class ContextBuildResult:
    messages: list[Message]
    tools: list[ToolSpec]
    compression: CompressionResult | None
    rules: tuple[SystemRuleBlock, ...]
    compression_error: str | None = None


class ContextBuilder:
    def __init__(
        self,
        *,
        rules: SystemRuleLoader,
        budget: TokenBudget,
        compressor: FixedCompressor,
    ) -> None:
        self._rules = rules
        self._budget = budget
        self._compressor = compressor

    async def compress_history(
        self,
        messages: Sequence[Message],
        *,
        existing_summary: str | None,
        target_remaining_tokens: int = 0,
    ) -> CompressionResult | None:
        return await self._compressor.compress(
            messages,
            existing_summary=existing_summary,
            target_remaining_tokens=target_remaining_tokens,
        )

    def estimate_messages(self, messages: Sequence[Message]) -> int:
        return self._budget.estimate_messages(messages)

    async def build(
        self,
        *,
        session_id: str,
        project_root: Path,
        messages: Sequence[Message],
        session_rules: str | None,
        existing_summary: str | None,
        skill_instructions: Sequence[str],
        additional_context: Sequence[str],
        tools: Sequence[ToolSpec],
    ) -> tuple[list[Message], list[ToolSpec], ContextBuildResult]:
        del session_id  # Reserved for future per-session rule sources.
        rule_blocks = self._rules.load(project_root, session_rules)
        system_parts = [self._rules.render(rule_blocks)]
        if additional_context:
            system_parts.append(
                "## Hook-provided context\n" + "\n\n".join(additional_context)
            )
        if skill_instructions:
            system_parts.append(
                "## Active skill instructions\n" + "\n\n".join(skill_instructions)
            )
        system_message = Message(role="system", content="\n\n".join(system_parts))

        history = list(messages)
        prefix = [system_message]
        if existing_summary:
            prefix.append(
                Message(
                    role="system",
                    content="## Previous session checkpoint\n" + existing_summary,
                )
            )
        candidate = prefix + history
        compression: CompressionResult | None = None
        compression_error: str | None = None
        if self._budget.estimate_total(candidate, tools) >= self._budget.trigger_tokens:
            fixed_tokens = self._budget.estimate_total(prefix, tools)
            # Reserve room for the generated checkpoint itself.
            summary_reserve = min(8_192, max(1_024, self._budget.context_window // 20))
            try:
                compression = await self._compressor.compress(
                    history,
                    existing_summary=existing_summary,
                    target_remaining_tokens=max(
                        0,
                        self._budget.target_tokens - fixed_tokens - summary_reserve,
                    ),
                )
            except Exception as exc:
                compression_error = f"{type(exc).__name__}: {exc}"
            if compression:
                history = history[compression.through_seq :]
                candidate = [
                    system_message,
                    Message(
                        role="system",
                        content="## Session checkpoint\n" + compression.summary,
                    ),
                    *history,
                ]
        result = ContextBuildResult(
            messages=candidate,
            tools=list(tools),
            compression=compression,
            rules=tuple(rule_blocks),
            compression_error=compression_error,
        )
        return result.messages, result.tools, result

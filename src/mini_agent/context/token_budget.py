from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from mini_agent.core.types import Message, ToolSpec


@dataclass(frozen=True)
class TokenBudget:
    context_window: int
    trigger_ratio: float = 0.75
    target_ratio: float = 0.50

    @property
    def trigger_tokens(self) -> int:
        return int(self.context_window * self.trigger_ratio)

    @property
    def target_tokens(self) -> int:
        return int(self.context_window * self.target_ratio)

    @staticmethod
    def estimate_text(text: str) -> int:
        # Conservative dependency-free estimate for mixed Chinese, English and code.
        return max(1, (len(text.encode("utf-8")) + 2) // 3)

    def estimate_messages(self, messages: Sequence[Message]) -> int:
        return sum(
            self.estimate_text(
                json.dumps(message.to_openai(), ensure_ascii=False, separators=(",", ":"))
            )
            + 4
            for message in messages
        )

    def estimate_tools(self, tools: Sequence[ToolSpec]) -> int:
        return sum(
            self.estimate_text(
                json.dumps(tool.to_openai(), ensure_ascii=False, separators=(",", ":"))
            )
            for tool in tools
        )

    def estimate_total(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> int:
        return self.estimate_messages(messages) + self.estimate_tools(tools)

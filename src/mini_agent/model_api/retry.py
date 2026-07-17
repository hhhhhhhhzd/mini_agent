from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
from typing import Callable


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for requests that have not produced model-visible output."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.25

    def delay_seconds(
        self,
        attempt: int,
        *,
        retry_after: float | None = None,
        random_value: Callable[[], float] = random.random,
    ) -> float:
        exponential = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** max(0, attempt - 1)),
        )
        jitter = exponential * self.jitter_ratio * max(0.0, random_value())
        return max(retry_after or 0.0, exponential + jitter)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None

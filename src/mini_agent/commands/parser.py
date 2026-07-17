from __future__ import annotations

from mini_agent.commands.models import ParsedCommand


def parse_command(text: str) -> ParsedCommand | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    command, _, remainder = stripped.partition(" ")
    name = command[1:].casefold()
    argument = remainder.strip() or None
    if name not in {"new", "list", "resume", "zip"}:
        return None
    return ParsedCommand(name=name, argument=argument)  # type: ignore[arg-type]

"""Typed errors so CLI/MCP layers can report *what* failed, not just that it failed."""

from __future__ import annotations

# failure categories (stable strings — scripts may match on them)
CONNECT = "connect"
FLASH = "flash"
SESSION = "session"
MEM = "mem"
SYMBOL = "symbol"
TARGET = "target"


class UdeError(RuntimeError):
    """A failed UDE operation, tagged with a category."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category

    def __str__(self) -> str:
        return f"[{self.category}] {super().__str__()}"

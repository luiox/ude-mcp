"""Symbol resolution from a GCC/HighTec linker .map file (offline, no UDE needed).

Three line shapes are recognized:

  table entry with bind column:
    ``0x70009108 0x7000910b      4 g g_demo_status  dsram0  .bss ...``

  table entry without bind column:
    ``0x70009100 0x70009103      4 g_demo_status``

  cross-view entry (address + name only):
    ``                0x0000000070009108                g_demo_status``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TABLE_RE = re.compile(
    r"^\s*0x([0-9a-fA-F]+)\s+0x[0-9a-fA-F]+\s+((?:0x)?[0-9a-fA-F]+)"
    r"\s+(?:([glw])\s+)?(\S+)")
_PLAIN_RE = re.compile(
    r"^\s+0x([0-9a-fA-F]{8,16})\s+([A-Za-z_][A-Za-z0-9_.]*)\s*$")

_WIDTH_FOR_SIZE = {1: 8, 2: 16}


@dataclass
class Symbol:
    name: str
    addr: int
    size: int = 0        # 0 = unknown (plain map entry)
    bind: str = ""       # g/l/w from the map, "" for plain entries

    @property
    def width(self) -> int:
        """Best-guess mem_read width from the symbol size."""
        return _WIDTH_FOR_SIZE.get(self.size, 32)

    def __str__(self) -> str:
        size = f" size={self.size}" if self.size else ""
        return f"0x{self.addr:08X} {self.name}{size}"


class SymbolTable:
    def __init__(self, symbols: dict[str, Symbol]):
        self._syms = symbols

    @classmethod
    def load(cls, map_path: str) -> "SymbolTable":
        p = Path(map_path)
        if not p.is_file():
            raise FileNotFoundError(f"map file not found: {map_path}")
        syms: dict[str, Symbol] = {}
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _TABLE_RE.match(line)
                if m:
                    name = m.group(4)
                    sym = Symbol(name=name, addr=int(m.group(1), 16),
                                 size=int(m.group(2), 0), bind=m.group(3) or "")
                    # keep the first (section-view) occurrence of a name
                    syms.setdefault(name, sym)
                    continue
                m = _PLAIN_RE.match(line)
                if m:
                    name = m.group(2)
                    if name in syms:
                        continue
                    sym = Symbol(name=name, addr=int(m.group(1), 16))
                    # only accept plain entries that look like user symbols
                    if not name.endswith((".c", ".h", ".o")):
                        syms[name] = sym
        return cls(syms)

    def resolve(self, name: str) -> Symbol | None:
        return self._syms.get(name)

    def search(self, pattern: str) -> list[Symbol]:
        """Case-insensitive regex search over symbol names."""
        rx = re.compile(pattern, re.IGNORECASE)
        return sorted((s for s in self._syms.values() if rx.search(s.name)),
                      key=lambda s: s.addr)

    def __len__(self) -> int:
        return len(self._syms)

"""Offline self-checks: no UDE install, no hardware, no network.

Runs the parts of ude-mcp that are pure Python: imports, the linker-map
symbol parser, config discovery, value parsing, CLI argument handling.
CI runs this on a plain Windows runner; on a bench machine it is a quick
sanity pass after changes.

    uv run python tests/test_offline.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from ude_mcp.config import load_config, load_conf_file  # noqa: E402
from ude_mcp.core import _to_int  # noqa: E402
from ude_mcp.errors import UdeError  # noqa: E402
from ude_mcp.symbols import SymbolTable  # noqa: E402

SYNTHETIC_MAP = """\
Section alignments
( addresses before main )

.text           0x0000000070000000     0x1000
 *(.text)
 .text          0x0000000070000000     0x40 AppSw/Main/Cpu0_Main.o
                0x0000000070000000                core0_main

.bss            0x0000000070009000     0x800
 *(.bss)
 .bss           0x0000000070009000       0x4 AppSw/Project/demo.o
                0x0000000070009004                local_counter
 0x0000000070009100 0x0000000070009103      4 g_demo_status
 0x0000000070009104 0x0000000070009104      1 g_demo_flag
 0x0000000070009108 0x000000007000910b      4 g_demo_array
"""


def test_symbol_table(tmp: Path) -> None:
    map_path = tmp / "demo.map"
    map_path.write_text(SYNTHETIC_MAP, encoding="utf-8")
    t = SymbolTable.load(str(map_path))

    s = t.resolve("g_demo_status")
    assert s is not None and s.addr == 0x70009100 and s.size == 4 and s.width == 32

    s = t.resolve("g_demo_flag")
    assert s is not None and s.size == 1 and s.width == 8

    assert t.resolve("core0_main") is not None       # plain cross-view entry
    assert t.resolve("missing_symbol") is None

    hits = t.search(r"^g_demo_")
    assert {h.name for h in hits} >= {"g_demo_status", "g_demo_flag", "g_demo_array"}
    print(f"  symbols: {len(t)} entries parsed, resolve/search/width OK")


def test_value_parsing() -> None:
    assert _to_int("3400") == 3400
    assert _to_int("0x10") == 16
    assert _to_int(77) == 77
    try:
        _to_int("null")
        raise AssertionError("expected UdeError")
    except UdeError as e:
        assert e.category == "mem"
    print("  value parsing OK")


def test_config(tmp: Path) -> None:
    conf = tmp / "ude-mcp.conf"
    conf.write_text(
        "# comment\nwsx = C:\\ws\\session.wsx\nresets = 5\n\nbadline\n",
        encoding="utf-8")
    kv = load_conf_file(str(conf))
    assert kv["wsx"] == r"C:\ws\session.wsx" and kv["resets"] == "5"

    cfg = load_config(conf=str(conf))
    assert cfg.wsx == r"C:\ws\session.wsx" and cfg.resets == 5
    assert cfg.connect_retries == 3            # default survived

    cfg2 = load_config(conf=str(conf), resets=1)
    assert cfg2.resets == 1                    # explicit override wins
    print("  config discovery/overrides OK")


def test_cli(tmp: Path) -> None:
    exe = str(SRC.parent / "src")  # noqa: F841 — uv run resolves the entry point
    r = subprocess.run(["uv", "run", "ude-mcp-cli", "--help"],
                       capture_output=True, text=True, cwd=str(SRC.parents[1]))
    assert r.returncode == 0 and "flash" in r.stdout, r.stderr

    map_path = tmp / "demo.map"
    map_path.write_text(SYNTHETIC_MAP, encoding="utf-8")
    r = subprocess.run(
        ["uv", "run", "ude-mcp-cli", "symbols", "^g_demo_", "--map", str(map_path)],
        capture_output=True, text=True, cwd=str(SRC.parents[1]))
    assert r.returncode == 0 and "g_demo_status" in r.stdout, r.stderr
    print("  CLI --help and offline `symbols` OK")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        import ude_mcp.cli, ude_mcp.core, ude_mcp.server  # noqa: F401,E402
        print("  imports OK")
        test_symbol_table(tmp)
        test_value_parsing()
        test_config(tmp)
        test_cli(tmp)
    print("ALL OFFLINE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

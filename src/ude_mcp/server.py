"""ude-mcp MCP tools — thin wrappers over ude_mcp.core.Ude.

Session model: one UDE instance per MCP server. Start it with
``ude_session_start`` (TSIM preset for hardware-free testing), then drive
the debugger through the other tools. Reliability rituals (connect retry,
flash retry, post-flash reset cycles, leaked-instance cleanup) live in
core.py and are shared with the CLI.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .core import Ude
from .errors import UdeError

mcp = FastMCP("ude-mcp")

_client: Ude | None = None
_client_lock = threading.Lock()

_TSIM_CFG_TEXT = """\
[Main]
Signature=UDE_TARGINFO_2.0
MCUs=Controller0
Description=Infineon TSIM TC1796 (TC1.3)

[Controller0]
Family=TriCore
Type=TC1796B
Enabled=1
traceStreams=none

[Controller0.Core]
Protocol=TC_GDI
Enabled=1

[Controller0.Core.TcGdiCoreTargIntf]
GdiDllPath=tsim.dll
MemoryCfgFilePath=TSIM\\MConfig_tc1
PeripheralsCfgFilePath=
InterruptCfgFilePath=
WriteSFRResetValues=0

[Controller0.PCP]
Master=Core
Enabled=0

[Controller0.LicenseCheck]
LicenseCheckMode=33
"""


def _tsim_cfg_path() -> str:
    cfg_dir = Path(tempfile.gettempdir()) / "ude-mcp"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "tsim_tc1796.cfg"
    if not cfg.exists():
        cfg.write_text(_TSIM_CFG_TEXT, encoding="utf-8")
    return str(cfg)


def _the_client() -> Ude:
    if _client is None or _client.session is None:
        raise UdeError("session", "no live UDE session; call ude_session_start first")
    return _client


def _call(cmd: str, bridge_timeout: float = 45.0, **args):
    return _the_client().call(cmd, bridge_timeout, **args)


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------

@mcp.tool()
def ude_session_start(
    cfg: str = "", wsx: str = "", tsim: bool = False, timeout: float = 240.0,
    kill_existing: bool = False, connect: bool = True,
) -> dict:
    """Launch a UDE instance with the automation bridge and optionally connect.

    Exactly one of:
      - cfg:  path to a UDE target configuration (.cfg) -> a fresh workspace is created
      - wsx:  path to an existing UDE workspace (.wsx) -> it is loaded
      - tsim: true -> use the built-in TriCore simulator (no hardware needed)
    kill_existing: first kill leaked UDE instances (they hold the DAP).
    connect: also connect to the target with retries (default true).
    """
    global _client
    with _client_lock:
        if _client is not None and _client.session is not None:
            _client.close()
        if tsim:
            overrides = dict(cfg=_tsim_cfg_path(), wsx="")
        else:
            overrides = dict(cfg=cfg, wsx=wsx)
        # empty-string overrides fall through to conf/env/defaults in load_config
        resolved = load_config(**overrides)
        client = Ude(resolved)
        client.cfg.start_timeout = timeout
        status = client.open(kill_existing=kill_existing, connect=connect)
        _client = client
        return status


@mcp.tool()
def ude_session_stop() -> str:
    """Close the UDE instance."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None
    return "stopped"


@mcp.tool()
def ude_status() -> dict:
    """Current UDE version, workspace, and per-core state snapshot."""
    return _the_client().status()


# ---------------------------------------------------------------------------
# target connection / program
# ---------------------------------------------------------------------------

@mcp.tool()
def ude_connect(timeout: float = 20.0, retries: int = 3) -> bool:
    """Connect to the target with retries (DAS connects are flaky)."""
    return bool(_the_client().connect(retries=retries, per_try=timeout))


@mcp.tool()
def ude_disconnect(timeout: float = 15.0) -> bool:
    """Disconnect from the target."""
    return bool(_call("disconnect", timeout + 10.0, timeout=timeout))


@mcp.tool()
def ude_load_program(path: str, core: int = 0) -> bool:
    """Load an ELF/program file into the core (RAM/debug, no flash programming)."""
    return bool(_call("load_program", path=path, core=core))


@mcp.tool()
def ude_flash(path: str, core: int = 0, flash_retries: int = 4,
              resets: int = 3, kill_existing: bool = True) -> dict:
    """Full flash ritual: halt -> LoadAndFlash (retried) -> reset+go xN -> running.

    The TC3xx needs several reset cycles after flashing or it comes up with
    weird bugs. Returns a summary (attempts used, final core state).
    """
    return _the_client().flash(path, flash_retries=flash_retries, resets=resets,
                               kill_existing=kill_existing, core=core)


# ---------------------------------------------------------------------------
# run control
# ---------------------------------------------------------------------------

@mcp.tool()
def ude_go(core: int = 0) -> bool:
    """Start/resume execution on the core."""
    _the_client().go(core)
    return True


@mcp.tool()
def ude_halt(core: int = 0) -> bool:
    """Halt execution on the core."""
    _the_client().halt(core)
    return True


@mcp.tool()
def ude_step(direction: str = "into", core: int = 0) -> bool:
    """Single step: direction = into | over | out."""
    cmds = {"into": "step_in", "over": "step_over", "out": "step_out"}
    if direction not in cmds:
        raise ValueError("direction must be into|over|out")
    return bool(_call(cmds[direction], core=core))


@mcp.tool()
def ude_reset(core: int = 0) -> bool:
    """Reset the target/core."""
    _the_client().reset(core)
    return True


@mcp.tool()
def ude_reset_and_run(resets: int = 3, core: int = 0) -> bool:
    """Reset+go once, then `resets` extra reset+go cycles (TC3xx quirk)."""
    _the_client().reset_and_run(resets=resets, core=core)
    return True


@mcp.tool()
def ude_wait_halt(core: int = 0, timeout: float = 5.0) -> bool:
    """Block until the core halts (e.g. at a breakpoint). Returns false on timeout."""
    return bool(_call("wait_halt", timeout + 10.0, core=core, timeout=timeout))


# ---------------------------------------------------------------------------
# breakpoints
# ---------------------------------------------------------------------------

@mcp.tool()
def ude_bp_add(desc: str, core: int = 0) -> int:
    """Add a breakpoint. desc = source location ('main.c 42') or an address/symbol expression."""
    return int(_call("bp_add", desc=desc, core=core))


@mcp.tool()
def ude_bp_remove(desc: str, core: int = 0) -> int:
    """Remove a breakpoint by the same desc used to add it."""
    return int(_call("bp_remove", desc=desc, core=core))


@mcp.tool()
def ude_bp_clear(core: int = 0) -> bool:
    """Remove all breakpoints on the core."""
    return bool(_call("bp_clear", core=core))


@mcp.tool()
def ude_bp_list(core: int = 0) -> list:
    """List breakpoints on the core."""
    return _call("bp_list", core=core)


# ---------------------------------------------------------------------------
# data access
# ---------------------------------------------------------------------------

@mcp.tool()
def ude_var_read(expr: str, core: int = 0):
    """Read a variable/expression (symbolic, via UDE expression evaluator)."""
    return _call("var_read", expr=expr, core=core)


@mcp.tool()
def ude_var_write(expr: str, value, core: int = 0) -> bool:
    """Write a variable/expression."""
    return bool(_call("var_write", expr=expr, value=value, core=core))


@mcp.tool()
def ude_read_symbol(spec: str, width: int = 0, core: int = 0) -> int:
    """Read an address ('0xD0000000') or a symbol name from the linker map.

    Symbol width is derived from the map size unless `width` (8|16|32) is given.
    """
    return _the_client().read(spec, width=width or None, core=core)


@mcp.tool()
def ude_symbols(pattern: str) -> list:
    """Search the linker map for symbols matching a regex (offline, no target access)."""
    return [str(s) for s in _the_client().symbol_table().search(pattern)]


@mcp.tool()
def ude_reg_read(name: str, core: int = 0):
    """Read a CPU register (e.g. 'PC', 'A[10]', 'D[0]')."""
    return _call("reg_read", name=name, core=core)


@mcp.tool()
def ude_reg_write(name: str, value, core: int = 0) -> bool:
    """Write a CPU register."""
    return bool(_call("reg_write", name=name, value=value, core=core))


@mcp.tool()
def ude_mem_read(addr, width: int = 32, core: int = 0) -> int:
    """Read one memory cell (raises a tagged error instead of returning junk).
    addr: hex string ('0xD0000000') or int. width: 8|16|32."""
    return _the_client().mem_read(int(addr, 0) if isinstance(addr, str) else addr,
                                  width=width, core=core)


@mcp.tool()
def ude_mem_write(addr, value, width: int = 32, core: int = 0) -> bool:
    """Write one memory cell (addr as in ude_mem_read)."""
    _the_client().mem_write(int(addr, 0) if isinstance(addr, str) else addr,
                            value, width=width, core=core)
    return True


@mcp.tool()
def ude_callstack(core: int = 0):
    """Get the current call stack of the halted core."""
    return _call("callstack", core=core)


@mcp.tool()
def ude_eval(expr: str, core: int = 0):
    """Evaluate a UDE expression and return its current value."""
    return _call("eval", expr=expr, core=core)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

"""High-level UDE client: the proven bench rituals, as a library.

Everything here was battle-tested in scratch scripts against real TC364
hardware; this module just makes the rituals first-class so the CLI and
MCP layers (and ad-hoc scripts) share one implementation.

  - leaked UDE instances hold the DAP -> kill before starting (flash flows)
  - ConnectTarget via DAS is flaky -> retry with backoff
  - LoadAndFlash fails on the first attempt after a fresh connect -> retry
    with a halt between attempts
  - TC3xx comes up with weird bugs after flashing unless reset several times
    -> reset+go cycles after a successful flash
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .config import UdeConfig, find_ude_exe
from .errors import CONNECT, FLASH, MEM, SYMBOL, TARGET, UdeError
from .session import UdeSession
from .symbols import SymbolTable

_UDE_PROCESS = "UDEVisualPlatform.exe"


def kill_existing_ude(timeout: float = 15.0) -> int:
    """Kill leaked UDE instances (they hold the DAP and block connect/flash).

    Returns the number of kill attempts made. Safe to call when none run.
    """
    r = subprocess.run(["taskkill", "/F", "/IM", _UDE_PROCESS],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 0  # nothing to kill (or tasklist access denied — nothing we can do)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chk = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {_UDE_PROCESS}"],
                             capture_output=True, text=True)
        if _UDE_PROCESS.lower() not in (chk.stdout or "").lower():
            return 1
        time.sleep(0.5)
    return 1


def _to_int(v) -> int:
    """Parse a bridge value (decimal string, hex string, int)."""
    if isinstance(v, int):
        return v
    s = str(v).strip()
    try:
        return int(s, 0)
    except ValueError:
        raise UdeError(MEM, f"unparseable value from UDE: {v!r}")


class Ude:
    """One UDE instance + bridge, with the bench rituals on top."""

    def __init__(self, cfg: UdeConfig):
        self.cfg = cfg
        self.session: UdeSession | None = None
        self._map: SymbolTable | None = None

    # -- lifecycle -----------------------------------------------------------

    def open(self, kill_existing: bool = False, connect: bool = True,
             connect_retries: int | None = None) -> dict:
        """Start UDE + bridge agent, then connect to the target with retries."""
        if self.session is not None:
            raise UdeError("session", "already open; call close() first")
        if kill_existing:
            kill_existing_ude()
        wsx, target_cfg = self.cfg.wsx, self.cfg.cfg
        if not wsx and not target_cfg:
            raise UdeError("config", "no wsx/cfg: pass --wsx/--cfg or set ude-mcp.conf")
        s = UdeSession()
        s.start(wsx=wsx or None, cfg=target_cfg or None,
                timeout=self.cfg.start_timeout,
                exe=find_ude_exe(self.cfg))
        self.session = s
        if connect:
            self.connect(retries=connect_retries)
        return self.status()

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.stop()
            finally:
                self.session = None

    def __enter__(self) -> "Ude":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- bridge plumbing -----------------------------------------------------

    def call(self, cmd: str, bridge_timeout: float = 45.0, **args):
        """Run one agent command; `bridge_timeout` bounds the local wait,
        remaining kwargs go to the agent (so an agent `timeout` arg is fine)."""
        self._ensure_session()
        return self.session.bridge.call(cmd, bridge_timeout, **args)

    def _ensure_session(self) -> UdeSession:
        if self.session is None:
            raise UdeError("session", "no live UDE session; call open() first")
        return self.session

    def status(self) -> dict:
        return self._ensure_session().status()

    # -- connection ----------------------------------------------------------

    def connect(self, retries: int | None = None, per_try: float = 25.0) -> bool:
        n = self.cfg.connect_retries if retries is None else retries
        last: Exception | None = None
        for i in range(max(1, n)):
            try:
                ok = self.call("connect", per_try + 15.0, timeout=per_try)
                if str(ok).lower() == "true":
                    return True
                last = UdeError(CONNECT, f"ConnectTarget returned {ok!r}")
            except Exception as e:  # noqa: BLE001 — keep retrying any DAS hiccup
                last = e
            if i < n - 1:
                time.sleep(3.0)
        raise UdeError(CONNECT, f"connect failed after {n} attempt(s): {last}")

    # -- run control ---------------------------------------------------------

    def go(self, core: int = 0) -> None:
        self.call("go", 30, core=core)

    def halt(self, core: int = 0) -> None:
        self.call("halt", 60, core=core)

    def reset(self, core: int = 0) -> None:
        self.call("reset", 30, core=core)

    def reset_and_run(self, resets: int | None = None, core: int = 0) -> None:
        """reset+go once, then `resets` extra reset+go cycles (TC3xx quirk)."""
        n = self.cfg.resets if resets is None else resets
        self.reset(core)
        self.go(core)
        time.sleep(2.0)
        for _ in range(max(0, n)):
            self.reset(core)
            time.sleep(0.5)
            self.go(core)
            time.sleep(1.5)

    # -- data access -----------------------------------------------------------

    def mem_read(self, addr: int, width: int = 32, core: int = 0) -> int:
        v = self.call("mem_read", 30, addr=addr, width=width, core=core)
        try:
            return _to_int(v)
        except UdeError:
            # running-target timing hiccups surface as junk here — say which addr
            raise UdeError(MEM, f"mem_read 0x{addr:08X} (w={width}) failed: {v!r}")

    def mem_write(self, addr: int, value: int, width: int = 32, core: int = 0):
        self.call("mem_write", 30, addr=addr, value=value, width=width, core=core)

    def var_read(self, expr: str, core: int = 0):
        """Read via the UDE expression evaluator (needs the ELF loaded)."""
        return self.call("var_read", 30, expr=expr, core=core)

    def eval(self, expr: str, core: int = 0):
        return self.call("eval", 30, expr=expr, core=core)

    # -- symbols -----------------------------------------------------------------

    def symbol_table(self) -> SymbolTable:
        if self._map is None:
            self._map = SymbolTable.load(self.cfg.resolved_map())
        return self._map

    def resolve(self, spec: str) -> tuple[int, int]:
        """'0xD0000000' | 'g_app_status'  ->  (addr, width)."""
        s = spec.strip()
        if re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", s):
            return int(s, 0), 32
        sym = self.symbol_table().resolve(s)
        if sym is not None:
            return sym.addr, sym.width
        # not in the map — let UDE's expression evaluator try (ELF symbols)
        raise UdeError(
            SYMBOL, f"symbol {spec!r} not in map; try: ude_var_read (ELF) "
                    f"or check --map")

    def read(self, spec: str, width: int | None = None, core: int = 0) -> int:
        """Read an address or symbol. Symbol width comes from the map size."""
        addr, w = self.resolve(spec)
        return self.mem_read(addr, width or w, core)

    # -- the flash ritual ---------------------------------------------------------

    def flash(
        self,
        elf: str,
        flash_retries: int | None = None,
        resets: int | None = None,
        kill_existing: bool = True,
        connect_retries: int | None = None,
        core: int = 0,
    ) -> dict:
        """connect -> halt -> flash (retried) -> reset_and_run -> leave RUNNING.

        Returns a summary dict; raises UdeError[flash] after the final retry.
        """
        path = str(Path(elf).resolve())
        if not Path(path).is_file():
            raise UdeError(FLASH, f"elf not found: {path}")

        was_open = self.session is not None
        if not was_open:
            self.open(kill_existing=kill_existing,
                      connect_retries=connect_retries)

        n = self.cfg.flash_retries if flash_retries is None else flash_retries
        attempts, last_err = 0, ""
        for i in range(max(1, n)):
            attempts = i + 1
            try:
                self.halt(core)
                time.sleep(1.0)
                r = self.call("flash", 600, path=path, core=core)
                if str(r).lower() == "true":
                    last_err = ""
                    break
                last_err = f"LoadAndFlash returned {r!r}"
            except Exception as e:  # noqa: BLE001 — retry any flash hiccup
                last_err = str(e)
            if i < n - 1:
                time.sleep(1.0)
        if last_err:
            raise UdeError(FLASH,
                           f"flash failed after {attempts} attempt(s): {last_err}")

        self.reset_and_run(resets=resets, core=core)
        return {
            "elf": path,
            "attempts": attempts,
            "resets": self.cfg.resets if resets is None else resets,
            "status": self.status(),
        }

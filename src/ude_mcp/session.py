"""UDE session management: locate UDE, render the bridge agent, run it."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from .bridge_server import BridgeServer

_ASSETS = Path(__file__).parent / "assets"

# UDE executable discovery: set UDE_MCP_UDE_EXE, or add your install path here,
# e.g. r"<UDE_INSTALL_DIR>\UDEVisualPlatform.exe"
_UDE_EXE_CANDIDATES: list[str] = [
    r"C:\Program Files\pls\UDE Starterkit 2021\UDEVisualPlatform.exe",
]


def find_ude_exe() -> str:
    env = os.environ.get("UDE_MCP_UDE_EXE")
    if env and Path(env).is_file():
        return env
    for cand in _UDE_EXE_CANDIDATES:
        if Path(cand).is_file():
            return cand
    raise RuntimeError(
        "UDEVisualPlatform.exe not found; set UDE_MCP_UDE_EXE to the full path"
    )


class UdeSession:
    """One running UDE instance with the bridge agent injected at startup."""

    def __init__(self) -> None:
        self.bridge = BridgeServer()
        self.proc: subprocess.Popen | None = None
        self._bridge_js: Path | None = None
        self._workdir = Path(tempfile.gettempdir()) / "ude-mcp"
        self._workdir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle ---------------------------------------------------------

    def start(
        self,
        wsx: str | None = None,
        cfg: str | None = None,
        timeout: float = 90.0,
        exe: str | None = None,
    ) -> dict:
        """Launch UDE with the bridge agent.

        Either pass ``cfg`` (a target configuration file) to create a fresh
        workspace, or ``wsx`` (an existing workspace file) to load it.
        ``exe`` overrides UDE executable discovery.
        """
        if self.proc is not None:
            raise RuntimeError("session already running; call stop() first")

        if not cfg and not wsx:
            raise ValueError("provide either cfg or wsx")
        mode = "create" if cfg else "load"
        wsx_path = str(Path(wsx or self._workdir / "session.wsx").resolve())
        cfg_path = str(Path(cfg).resolve()) if cfg else ""

        self.bridge.start()
        js = self._render_bridge(port=self.bridge.port, wsx=wsx_path, cfg=cfg_path, mode=mode)
        self._bridge_js = self._workdir / "ude_bridge.js"
        self._bridge_js.write_text(js, encoding="utf-8")

        self.proc = subprocess.Popen([(exe or find_ude_exe()), f"-s{self._bridge_js}"])

        if not self.bridge.wait_for_agent(timeout):
            self.stop()
            raise TimeoutError(f"UDE bridge agent did not come up within {timeout}s")
        return self.status()

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.bridge.call("shutdown", 10)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        self.bridge.stop()

    def ensure_running(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("no live UDE session; call ude_session_start first")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _render_bridge(port: int, wsx: str, cfg: str, mode: str) -> str:
        tpl = (_ASSETS / "bridge.js").read_text(encoding="utf-8")

        def jesc(s: str) -> str:
            return s.replace("\\", "\\\\")

        return (
            tpl.replace("__PORT__", str(port))
            .replace("__WSX__", jesc(wsx))
            .replace("__CFG__", jesc(cfg))
            .replace("__MODE__", mode)
        )

    def status(self) -> dict:
        self.ensure_running()
        return self.bridge.last_status or {}

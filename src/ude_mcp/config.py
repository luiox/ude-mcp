"""Configuration discovery: CLI flags > env > conf file > built-in defaults.

Conf file is flat ``key = value`` (no TOML dependency), searched at
``./ude-mcp.conf`` then ``~/.ude-mcp.conf``.  Recognized keys::

    ude_exe = C:\\Program Files\\pls\\UDE Starterkit 2021\\UDEVisualPlatform.exe
    wsx     = C:\\path\\to\\workspace.wsx
    cfg     = C:\\path\\to\\target.cfg
    elf     = C:\\path\\to\\firmware.elf
    map     = C:\\path\\to\\firmware.map
    resets  = 3
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

CONF_NAMES = ("ude-mcp.conf",)

# known installs tried when neither env nor conf provide ude_exe
_UDE_CANDIDATES = [
    r"C:\Program Files\pls\UDE Starterkit 2021\UDEVisualPlatform.exe",
]


@dataclass
class UdeConfig:
    ude_exe: str = ""
    wsx: str = ""
    cfg: str = ""
    elf: str = ""
    map: str = ""
    resets: int = 3                # extra reset+go cycles after flash (TC3xx quirk)
    connect_retries: int = 3
    flash_retries: int = 4
    start_timeout: float = 240.0   # UDE cold start is slow; scripts use 240s

    # -- resolution helpers -------------------------------------------------

    def resolved_elf(self) -> str:
        p = self.elf
        if p and Path(p).is_file():
            return p
        raise FileNotFoundError(
            f"elf not found: {p!r} (pass it, or set 'elf' in ude-mcp.conf)")

    def resolved_map(self) -> str:
        """Map file: explicit, else sit next to the elf."""
        if self.map and Path(self.map).is_file():
            return self.map
        if self.elf:
            cand = Path(self.elf).with_suffix(".map")
            if cand.is_file():
                return str(cand)
        raise FileNotFoundError(
            "no symbol map (pass --map, set 'map' in ude-mcp.conf, "
            "or place a .map next to the elf)")

    def with_overrides(self, **kw) -> "UdeConfig":
        """Non-None/empty overrides applied on top of this config."""
        cur = self
        for k, v in kw.items():
            if v is None or v == "":
                continue
            cur = replace(cur, **{k: v})
        return cur


def load_conf_file(path: str | None) -> dict:
    if path:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"conf file not found: {path}")
        return _parse_conf(p)
    for name in CONF_NAMES:
        p = Path.cwd() / name
        if p.is_file():
            return _parse_conf(p)
    home = Path.home() / ("." + CONF_NAMES[0])   # ~/.ude-mcp.conf
    if home.is_file():
        return _parse_conf(home)
    return {}


def _parse_conf(p: Path) -> dict:
    out: dict = {}
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"')
        if not k or not v:
            continue
        out[k] = v
    return out


def load_config(
    conf: str | None = None,
    **overrides,
) -> UdeConfig:
    """Build the effective config: defaults < conf file < env < overrides."""
    kv = load_conf_file(conf)

    def get(key: str) -> str:
        if key in overrides and overrides[key]:
            return str(overrides[key])
        env_key = f"UDE_MCP_{key.upper()}"
        if os.environ.get(env_key):
            return os.environ[env_key]
        return kv.get(key, "")

    cfg = UdeConfig(
        ude_exe=get("ude_exe"),
        wsx=get("wsx"),
        cfg=get("cfg"),
        elf=get("elf"),
        map=get("map"),
        resets=_to_int(get("resets"), 3),
        connect_retries=_to_int(get("connect_retries"), 3),
        flash_retries=_to_int(get("flash_retries"), 4),
    )
    # never set via env/conf: explicit overrides only
    for k in ("resets", "connect_retries", "flash_retries", "start_timeout"):
        if overrides.get(k) is not None:
            setattr(cfg, k, overrides[k])
    return cfg


def find_ude_exe(cfg: UdeConfig) -> str:
    if cfg.ude_exe and Path(cfg.ude_exe).is_file():
        return cfg.ude_exe
    env = os.environ.get("UDE_MCP_UDE_EXE")
    if env and Path(env).is_file():
        return env
    for cand in _UDE_CANDIDATES:
        if Path(cand).is_file():
            return cand
    raise FileNotFoundError(
        "UDEVisualPlatform.exe not found; set UDE_MCP_UDE_EXE, "
        "'ude_exe' in ude-mcp.conf, or pass --ude-exe")


def _to_int(v: str, dflt: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return dflt

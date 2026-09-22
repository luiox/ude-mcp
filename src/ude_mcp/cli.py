"""ude-mcp CLI — drive UDE from the shell, no MCP client needed.

    uv run ude-mcp-cli flash  C:\\proj\\firmware.elf         # full ritual
    uv run ude-mcp-cli read   g_app_status 0xD0000000        # symbol or address
    uv run ude-mcp-cli watch  g_app_speed --interval 0.5
    uv run ude-mcp-cli symbols g_app                          # offline map search
    uv run ude-mcp-cli status | runctl go|halt|reset

Paths and defaults come from ./ude-mcp.conf or ~/.ude-mcp.conf (see config.py);
every flag can override.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .config import load_config
from .core import Ude
from .errors import UdeError


def _build_parser() -> argparse.ArgumentParser:
    # shared path options: accepted both before and after the subcommand
    common = argparse.ArgumentParser(add_help=False)
    for flags, kw in (
        (("--conf",), dict(help="conf file (default: ./ude-mcp.conf or ~/.ude-mcp.conf)")),
        (("--wsx",), dict(help="UDE workspace (.wsx)")),
        (("--cfg",), dict(help="UDE target configuration (.cfg)")),
        (("--elf",), dict(help="default ELF for flash")),
        (("--map",), dict(help="linker .map for symbol resolution")),
        (("--ude-exe",), dict(dest="ude_exe", help="UDEVisualPlatform.exe path")),
        (("--start-timeout",),
         dict(type=float, help="seconds to wait for the UDE bridge agent (default 240)")),
        (("--connect-retries",),
         dict(type=int, help="DAS connect attempts (default 3)")),
    ):
        common.add_argument(*flags, default=argparse.SUPPRESS, **kw)

    p = argparse.ArgumentParser(
        prog="ude-mcp-cli", parents=[common],
        description="Drive PLS UDE from the command line (flash / read / watch).")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help, **kw):
        return sub.add_parser(name, parents=[common], help=help, **kw)

    fp = add("flash", "connect -> halt -> flash (retried) -> reset xN -> run")
    fp.add_argument("elf", nargs="?", help="ELF to flash (default from conf)")
    fp.add_argument("--resets", type=int, help="extra reset+go cycles after flash (default 3)")
    fp.add_argument("--flash-retries", type=int, help="LoadAndFlash attempts (default 4)")
    fp.add_argument("--no-kill", action="store_true",
                    help="do not kill leaked UDE instances first (default: kill)")

    rp = add("read", "read symbols/addresses once (target keeps running)")
    rp.add_argument("specs", nargs="+", metavar="SPEC",
                    help="symbol name or 0x address")
    rp.add_argument("--width", type=int, choices=(8, 16, 32), help="force read width")
    rp.add_argument("--dec", action="store_true", help="print decimal instead of hex")

    rp = add("write", "write one symbol/address")
    rp.add_argument("spec", metavar="SPEC")
    rp.add_argument("value", type=lambda x: int(x, 0))
    rp.add_argument("--width", type=int, choices=(8, 16, 32))

    wp = add("watch", "poll symbols/addresses periodically")
    wp.add_argument("specs", nargs="+", metavar="SPEC")
    wp.add_argument("--interval", type=float, default=0.5)
    wp.add_argument("--count", type=int, default=0, help="0 = until Ctrl+C")
    wp.add_argument("--dec", action="store_true")
    wp.add_argument("--json", action="store_true", help="machine-readable output")

    rp = add("runctl", "go | halt | reset")
    rp.add_argument("action", choices=("go", "halt", "reset"))

    add("status", "UDE + target state snapshot")

    sp = add("symbols", "search the linker map (offline, no UDE)")
    sp.add_argument("pattern", help="regex over symbol names")
    sp.add_argument("--list-all", action="store_true", help="ignore pattern, list everything")

    return p


def _connect_opts(args) -> dict:
    return dict(
        conf=getattr(args, "conf", None), wsx=getattr(args, "wsx", None),
        cfg=getattr(args, "cfg", None), elf=getattr(args, "elf", None),
        map=getattr(args, "map", None), ude_exe=getattr(args, "ude_exe", None),
        start_timeout=getattr(args, "start_timeout", None),
    )


def _open_client(args, kill: bool = False, connect: bool = True,
                 connect_retries: int | None = None) -> Ude:
    cfg = load_config(**_connect_opts(args))
    u = Ude(cfg)
    u.open(kill_existing=kill, connect=connect,
           connect_retries=connect_retries
           if connect_retries is not None else getattr(args, "connect_retries", None))
    return u


def _fmt(v: int, dec: bool) -> str:
    return str(v) if dec else f"0x{v:08X}"


def _cmd_flash(args) -> int:
    cfg = load_config(**_connect_opts(args))
    elf = args.elf or cfg.elf
    if not elf:
        print("error: no elf given and none in conf (--elf / 'elf=' in ude-mcp.conf)",
              file=sys.stderr)
        return 2
    u = Ude(cfg)
    try:
        summary = u.flash(
            elf,
            flash_retries=args.flash_retries,
            resets=args.resets,
            kill_existing=not args.no_kill,
            connect_retries=args.connect_retries,
        )
    except UdeError as e:
        print(f"error: {e}", file=sys.stderr)
        u.close()
        return 1
    print(f"flash OK: {summary['elf']} "
          f"(attempts={summary['attempts']}, extra resets={summary['resets']})")
    for c in summary["status"].get("cores", []):
        print(f"  core{c.get('index')}: name={c.get('name')} "
              f"connected={c.get('connected')} running={c.get('running')}")
    u.close()
    return 0


def _cmd_read(args) -> int:
    u = _open_client(args)
    rc = 0
    try:
        for spec in args.specs:
            try:
                v = u.read(spec, width=args.width)
            except UdeError as e:
                print(f"{spec}: error: {e}", file=sys.stderr)
                rc = 1
                continue
            print(f"{spec} = {_fmt(v, args.dec)}")
    finally:
        u.close()
    return rc


def _cmd_write(args) -> int:
    u = _open_client(args)
    try:
        addr, w = u.resolve(args.spec)
        u.mem_write(addr, args.value, width=args.width or w)
        back = u.mem_read(addr, args.width or w)
        print(f"{args.spec} <- 0x{args.value:X} (readback {_fmt(back, False)})")
    finally:
        u.close()
    return 0


def _cmd_watch(args) -> int:
    u = _open_client(args)
    rows = []
    rc = 0
    try:
        n = 0
        while True:
            row = {}
            for spec in args.specs:
                try:
                    v = u.read(spec)
                    row[spec] = v if args.json else _fmt(v, args.dec)
                except UdeError as e:
                    row[spec] = f"<{e.category}>" if not args.json else None
                    rc = 1
            if args.json:
                rows.append(row)
                print(json.dumps({"n": n, **row}), flush=True)
            else:
                print(f"[{n:4d}] " + "  ".join(f"{k}={v}" for k, v in row.items()),
                      flush=True)
            n += 1
            if args.count and n >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        u.close()
    return rc


def _cmd_runctl(args) -> int:
    u = _open_client(args)
    try:
        getattr(u, args.action)()
        st = u.status()
        for c in st.get("cores", []):
            print(f"core{c.get('index')}: running={c.get('running')} "
                  f"state={c.get('state')} at={c.get('location')}")
    finally:
        u.close()
    return 0


def _cmd_status(args) -> int:
    u = _open_client(args, connect=False)
    try:
        st = u.status()
        print(json.dumps(st, indent=2, ensure_ascii=False))
    finally:
        u.close()
    return 0


def _cmd_symbols(args) -> int:
    from .symbols import SymbolTable
    cfg = load_config(**_connect_opts(args))
    try:
        table = SymbolTable.load(cfg.resolved_map())
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    syms = table.search(args.pattern) if not args.list_all else table.search(".")
    if not syms:
        print(f"no symbols matching {args.pattern!r} "
              f"({len(table)} loaded from {cfg.resolved_map()})")
        return 1
    for s in syms:
        print(s)
    print(f"-- {len(syms)} of {len(table)} symbols", file=sys.stderr)
    return 0


_HANDLERS = {
    "flash": _cmd_flash,
    "read": _cmd_read,
    "write": _cmd_write,
    "watch": _cmd_watch,
    "runctl": _cmd_runctl,
    "status": _cmd_status,
    "symbols": _cmd_symbols,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _HANDLERS[args.cmd](args)
    except UdeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

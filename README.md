# ude-mcp

English | [中文](README_CN.md)

[![smoke](https://github.com/luiox/ude-mcp/actions/workflows/smoke.yml/badge.svg)](https://github.com/luiox/ude-mcp/actions/workflows/smoke.yml)

Drive the **PLS UDE® (Universal Debug Engine)** debugger from an AI agent or
from your shell. One core, two frontends: an **MCP server** and a plain
**CLI** — the CLI is handy when no MCP client is around (or the other way).

Works against real hardware over DAS/JTAG, not just the simulator.

## Verified on hardware

The test bench: **Infineon AURIX TC364 TriBoard** (SAK-TC364DP-64F300F),
UDE Starterkit 2021 (build 8417), DAS over JTAG, Windows 11.
This tool has been the daily flash-and-debug loop of a real TriCore
firmware project for weeks of bring-up and regression. Every claim below
traces to bench runs — the counts are `bridge.call` sites in the bench
scripts that ran against the board.

| Feature | TC364 hardware | Notes |
| --- | --- | --- |
| Session start (launch UDE + inject bridge agent) | ✅ verified | every bench session |
| Connect via DAS, with retry | ✅ verified | 40+ connects incl. flaky-DAS recovery |
| Flash (`LoadAndFlash`, retried) | ✅ verified | 7+ flash+verify runs; this ritual is what `flash` runs |
| Reset + post-flash reset×N cycles | ✅ verified | TC3xx comes up broken without them |
| Go / halt | ✅ verified | ~80 / ~10 uses |
| Status snapshot (cores, run state) | ✅ verified | |
| `mem_read` 32-bit — halted **and while running** | ✅ verified | SFRs (port OUT), CAN node regs (CCCR/PSR/ECR/IR/TXBRP/TXBTO), CAN message RAM |
| `mem_write` 32-bit (RAM) | ✅ verified | runtime variable patching |
| `reg_read` (PC) | ✅ verified | |
| `var_read` (UDE expression evaluator) | ✅ verified | sampled a FreeRTOS task counter on the live target |
| Symbol read via linker map (`read g_var`) | ✅ verified | 8225-symbol HighTec map, width derived from symbol size |
| CLI one-liners (`read` on live board) | ✅ verified | 2026-09-22 |

**TSIM simulator only** (prototype e2e): `wait_halt`, struct-member `var_read`.

**Implemented but not yet exercised on real hardware** — use with care,
PRs with bench evidence welcome:

`step` · breakpoints (`bp_*`) · `callstack` · `eval` · `var_write` ·
`load_program` · `disconnect` · 8/16-bit memory widths

## How it works

UDE Starterkit cannot be automated from outside its process (the COM launcher
is not shipped/registered in the free toolchain), so ude-mcp injects a JScript
**bridge agent** into UDE as a startup macro (`UDE.exe -s<agent.js>`). The
agent binds to the live UDE object model and talks back over localhost HTTP
long-polling:

```
AI client ⇄ (MCP/stdio or CLI) ⇄ ude-mcp (Python) ⇄ (localhost HTTP) ⇄ bridge.js (in UDE) ⇄ UDE object model ⇄ target
```

Everything uses officially supported UDE mechanisms (startup macros + the COM
object model) — no patching, no unsupported hacks. See
[docs/api-selection.md](docs/api-selection.md).

## The reliability rituals (why it works on a real bench)

These live in `core.py`, shared by CLI and MCP, instead of rotting in
ad-hoc scripts:

1. **Leaked UDE instances hold the DAP.** `flash` kills leftovers first.
2. **DAS connect is flaky.** Connect is retried with backoff (default 3×).
3. **`LoadAndFlash` often fails on the first attempt after a fresh connect.**
   Retried with a halt between attempts (default 4×).
4. **TC3xx comes up with weird bugs unless reset several times after flashing.**
   Every flash ends with reset+go cycles (default 3).

## Setup

Prerequisites: Windows, UDE Starterkit installed, [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

One-time config — `~/.ude-mcp.conf` (or `./ude-mcp.conf`), flat `key = value`:

```ini
wsx = C:\path\to\workspace.wsx            # UDE workspace
cfg = C:\path\to\TriBoard_TC36xA.cfg      # or a target config to create one
elf = C:\path\to\project.elf              # default for flash
map = C:\path\to\project.map              # linker map for symbol reads
resets = 3
```

Missing `ude_exe` is resolved from `UDE_MCP_UDE_EXE`, then known install
paths. Every key can be overridden per-invocation
(`--wsx/--cfg/--elf/--map/--ude-exe/...`), before or after the subcommand.

## CLI

```bash
uv run ude-mcp-cli flash                  # connect -> halt -> flash -> reset x3 -> run
uv run ude-mcp-cli flash other.elf --resets 5
uv run ude-mcp-cli read g_app_status 0xD0000000    # target keeps running
uv run ude-mcp-cli watch g_app_speed --interval 0.5
uv run ude-mcp-cli write g_some_var 0
uv run ude-mcp-cli runctl go|halt|reset
uv run ude-mcp-cli status
uv run ude-mcp-cli symbols '^g_app'       # offline map search, no UDE started
```

Notes:

- `read`/`watch` never halt the target — DAP reads work while it runs.
- Symbol width (8/16/32) comes from the map's size column; force with `--width`.
- `flash` kills leaked UDE instances first; `--no-kill` keeps your GUI session.
- `watch --json` emits machine-readable lines for scripts.

## MCP server

```json
{
  "mcpServers": {
    "ude": {
      "command": "uv",
      "args": ["--directory", "path/to/ude-mcp", "run", "ude-mcp"]
    }
  }
}
```

Same core, same rituals. Tools: `ude_session_start` (`kill_existing`,
`connect`), `ude_flash` (retries + reset cycles built in), `ude_read_symbol`
(map-based, width-aware), `ude_symbols`, run control (`ude_go/halt/reset/
reset_and_run/step/wait_halt`), `ude_bp_*`, `ude_var_read/var_write`,
`ude_reg_read/write`, `ude_mem_read/write`, `ude_callstack`, `ude_eval`,
`ude_status`. Failures surface as tagged errors — `[connect]`, `[flash]`,
`[mem]`, `[symbol]` — instead of silent `None`s.

## Layout

```
src/ude_mcp/
  bridge_server.py  localhost HTTP broker <-> in-UDE JScript agent
  session.py        UDE process + bridge agent lifecycle
  core.py           high-level API: the bench rituals (connect/flash/read)
  symbols.py        GCC/HighTec .map parser — symbol -> address/width, offline
  config.py         conf-file/env discovery
  cli.py            command-line interface
  server.py         MCP server (thin wrappers over core)
tests/test_offline.py  no-UDE self-checks (CI runs this)
scratch/              bench history: the scripts that produced the evidence
```


## License

[MIT](LICENSE)

# ude-mcp

MCP (Model Context Protocol) server for the **PLS UDE® (Universal Debug Engine)** debugger.
It lets AI coding agents drive UDE over the official **UDE Object Model** automation API:
load a workspace, connect a target, flash an ELF, run/halt/step, set breakpoints, and read
back variables, registers, memory, and call stacks.

## Status

✅ **Working prototype** — end-to-end verified against the built-in TSIM simulator
(session start → connect → register/memory reads → run control → clean shutdown).
Real-hardware (AURIX TriCore via DAP/JTAG) testing is next.

### How it works

UDE Starterkit cannot be automated from outside its process (the COM launcher is
not shipped/registered in the free toolchain), so ude-mcp injects a JScript
**bridge agent** into UDE as a startup macro (`UDE.exe -s<agent.js>`). The agent
binds to the live UDE object model and talks to the MCP server over localhost
HTTP long-polling:

```
AI client ⇄ (MCP/stdio) ⇄ ude-mcp (Python) ⇄ (localhost HTTP) ⇄ bridge.js (in UDE) ⇄ UDE object model ⇄ TSIM / target
```

Everything uses officially supported UDE mechanisms (startup macros + the COM
object model); no patching or unsupported hacks. See
[docs/api-selection.md](docs/api-selection.md) for the full API assessment.

## Target platform

- Windows only (UDE is a Windows application; the automation API is COM-based)
- UDE Starterkit / UDE installed with the HighTec TriCore toolchain
- First milestone: drive the built-in **TSIM simulator** (no hardware needed),
  then real AURIX TriCore targets over DAP/JTAG

## Planned MCP tools

| Tool | Description |
| --- | --- |
| `session.status` | Report UDE installation, COM registration and session state |
| `session.open_workspace` | Load a UDE workspace (`.wsx`) |
| `session.connect` / `disconnect` | Target connect (with timeout / wait helpers) |
| `program.load` | Download an ELF (optionally flash + verify) |
| `run` / `halt` / `step` / `reset` | Core run control (multi-core aware) |
| `breakpoint.*` | Set/clear/enable code & data breakpoints |
| `var.read` / `var.write` | Read/write symbols/expressions via UDE expression evaluator |
| `reg.read` / `reg.write` | Core register access |
| `mem.read` / `mem.write` | Raw memory access (8/16/32-bit, arrays) |
| `debug.callstack` | Call stack (incl. TriCore CSA-based frames) |
| `debug.state` | Core state, current location, halt reason |

## Development

```powershell
uv sync
uv run ude-mcp   # stdio MCP server
```

Smoke test (launches a real UDE instance against the TSIM simulator):

```powershell
uv run python scratch/e2e.py
```

### MCP client configuration

```json
{
  "mcpServers": {
    "ude": {
      "command": "uv",
      "args": ["--directory", "path/to/ude-mcp", "run", "ude-mcp"],
      "env": { "UDE_MCP_UDE_EXE": "<UDE_INSTALL_DIR>\\UDEVisualPlatform.exe" }
    }
  }
}
```

## License

[MIT](LICENSE)

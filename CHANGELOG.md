# Changelog

## 0.1.0 — 2026-09-22

First tagged release. Real-hardware proven on an Infineon AURIX TC364
TriBoard over DAS/JTAG (verification matrix in the README).

### Added

- **CLI** (`ude-mcp-cli`): `flash` / `read` / `write` / `watch` / `runctl` /
  `status` / `symbols` — usable without any MCP client.
- **Linker-map symbol resolution** (`symbols.py`): symbol → address/width
  from GCC/HighTec `.map` files, offline; width (8/16/32) derived from the
  symbol size.
- **Reliability rituals in `core.py`** (previously copy-pasted across bench
  scripts): leaked-UDE cleanup, DAS connect retry, `LoadAndFlash` retry,
  post-flash reset+go cycles.
- **Config discovery** (`config.py`): `./ude-mcp.conf` / `~/.ude-mcp.conf`
  for wsx/cfg/elf/map/resets; UDE path candidates built in.
- **Tagged errors** (`errors.py`): `[connect]` / `[flash]` / `[mem]` /
  `[symbol]` instead of silent `None`s.
- Offline self-checks (`tests/test_offline.py`) + GitHub Actions smoke job.
- `README_CN.md` (中文文档, at repo root).

### Changed

- MCP server (`server.py`) is now a thin layer over `core.Ude`; tool names
  unchanged. `ude_session_start` gains `kill_existing`/`connect`;
  `ude_flash` runs the full retry + reset ritual; new `ude_read_symbol`,
  `ude_symbols`, `ude_reset_and_run`.
- `UdeSession.start()` accepts an explicit `exe`.

### Verification summary

- TC364 hardware: session/connect/flash/reset/go/halt/status, 32-bit
  memory read (halted + running) and write, `reg_read`, `var_read`,
  symbol-based live reads via the new CLI.
- TSIM only: `wait_halt`, struct-member `var_read`.
- Not yet exercised on hardware: `step`, breakpoints, `callstack`, `eval`,
  `var_write`, `load_program`, `disconnect`, 8/16-bit widths.

# ude-mcp

[English](README.md) | 中文

[![smoke](https://github.com/luiox/ude-mcp/actions/workflows/smoke.yml/badge.svg)](https://github.com/luiox/ude-mcp/actions/workflows/smoke.yml)

用 AI 代理或命令行驱动 **PLS UDE®（Universal Debug Engine）调试器**。一个内核，两个前端：**MCP server** 和纯 **CLI**——没装 MCP 客户端的时候 CLI 更省事（反之亦然）。

支持 DAS/JTAG 连真板，不只是仿真器。

## 实板验证情况

测试平台：**英飞凌 AURIX TC364 TriBoard**（SAK-TC364DP-64F300F）、
UDE Starterkit 2021（build 8417）、DAS over JTAG、Windows 11。
本工具是一个真实 TriCore 固件项目数周 bring-up 与回归测试期间的日常
烧录+调试工具。下表每一条都有台架记录可查——次数是台架脚本里对板子
执行过的 `bridge.call` 调用统计。

| 功能 | TC364 实板 | 说明 |
| --- | --- | --- |
| 会话启动（拉起 UDE + 注入 bridge agent） | ✅ 已验证 | 每次上板都用 |
| DAS 连接（带重试） | ✅ 已验证 | 40+ 次连接，含 DAS 抽风后的自愈重试 |
| 烧录（`LoadAndFlash`，带重试） | ✅ 已验证 | 7+ 次烧录+校验；`flash` 命令跑的就是这套流程 |
| 复位 + 烧录后多次复位 | ✅ 已验证 | TC3xx 不多次复位会带病上电 |
| go / halt | ✅ 已验证 | 约 80 / 10 次 |
| status 快照（核状态、运行态） | ✅ 已验证 | |
| `mem_read` 32 位——halt 态和**运行态**都行 | ✅ 已验证 | SFR（端口 OUT）、CAN 节点寄存器（CCCR/PSR/ECR/IR/TXBRP/TXBTO）、CAN 消息 RAM |
| `mem_write` 32 位（RAM） | ✅ 已验证 | 运行时变量补丁测试 |
| `reg_read`（PC） | ✅ 已验证 | |
| `var_read`（UDE 表达式求值器） | ✅ 已验证 | 在运行中的目标上采样 FreeRTOS 任务计数器 |
| 通过链接 map 读符号（`read g_var`） | ✅ 已验证 | 8225 个符号的 HighTec map，宽度按符号大小推导 |
| CLI 一条命令读在板变量 | ✅ 已验证 | 2026-09-22 |

**仅 TSIM 仿真器验证过**（原型 e2e）：`wait_halt`、结构体成员 `var_read`。

**已实现但尚未在实板上用过**——请谨慎使用，欢迎带台架证据的 PR：

`step` · 断点（`bp_*`）· `callstack` · `eval` · `var_write` ·
`load_program` · `disconnect` · 8/16 位内存读写

## 工作原理

UDE Starterkit 无法从外部进程自动化（免费版不附带/不注册 COM 启动器），
所以 ude-mcp 把一段 JScript **bridge 代理**作为启动宏注入 UDE
（`UDE.exe -s<agent.js>`）。代理绑定到 UDE 运行中的对象模型，通过本机
HTTP 长轮询与 Python 端通信：

```
AI 客户端 ⇄ (MCP/stdio 或 CLI) ⇄ ude-mcp (Python) ⇄ (本机 HTTP) ⇄ bridge.js（UDE 内） ⇄ UDE 对象模型 ⇄ 目标板
```

全部使用官方支持的机制（启动宏 + COM 对象模型）——不打补丁、无黑科技。
API 选型见 [api-selection.md](docs/api-selection.md)。

## 可靠性仪式（真机上能用的关键）

这些都在 `core.py` 里，CLI 和 MCP 共享，不再散落在临时脚本里：

1. **泄漏的 UDE 实例占着 DAP。** `flash` 先清干净。
2. **DAS 连接经常抽风。** connect 带退避重试（默认 3 次）。
3. **新连接后第一次 `LoadAndFlash` 经常失败。** halt 后重试（默认 4 次）。
4. **TC3xx 烧完不多复位几次会带病运行。** 每次烧录后做 reset+go 循环（默认 3 次）。

## 安装配置

前置：Windows、已装 UDE Starterkit、[uv](https://docs.astral.sh/uv/)。

```bash
uv sync
```

一次性配置——`~/.ude-mcp.conf`（或 `./ude-mcp.conf`），扁平的 `key = value`：

```ini
wsx = C:\path\to\workspace.wsx            # UDE 工作区
cfg = C:\path\to\TriBoard_TC36xA.cfg      # 或用目标配置新建工作区
elf = C:\path\to\project.elf              # flash 默认文件
map = C:\path\to\project.map              # 符号读取用的链接 map
resets = 3
```

`ude_exe` 缺省时依次找 `UDE_MCP_UDE_EXE` 环境变量、已知安装路径。
所有配置项都可以在命令行覆盖（`--wsx/--cfg/--elf/--map/--ude-exe/...`），
放在子命令前面后面都行。

## CLI

```bash
uv run ude-mcp-cli flash                  # 连接 -> halt -> 烧录 -> 复位x3 -> 运行
uv run ude-mcp-cli flash other.elf --resets 5
uv run ude-mcp-cli read g_app_status 0xD0000000    # 目标保持运行
uv run ude-mcp-cli watch g_app_speed --interval 0.5
uv run ude-mcp-cli write g_some_var 0
uv run ude-mcp-cli runctl go|halt|reset
uv run ude-mcp-cli status
uv run ude-mcp-cli symbols '^g_app'       # 离线查 map，不起 UDE
```

说明：

- `read`/`watch` 不 halt 目标——DAP 读取在运行态就能做。
- 符号宽度（8/16/32）按 map 里的 size 列推导；可用 `--width` 强制。
- `flash` 默认先杀泄漏的 UDE 实例；想保留手动打开的 UDE 用 `--no-kill`。
- `watch --json` 输出机器可读格式，方便脚本消费。

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

同一内核、同一套仪式。工具：`ude_session_start`（带 `kill_existing`、
`connect`）、`ude_flash`（内置重试+多次复位）、`ude_read_symbol`（基于
map、宽度自动推导）、`ude_symbols`、运行控制（`ude_go/halt/reset/
reset_and_run/step/wait_halt`）、`ude_bp_*`、`ude_var_read/var_write`、
`ude_reg_read/write`、`ude_mem_read/write`、`ude_callstack`、`ude_eval`、
`ude_status`。失败会带类别标签抛出——`[connect]`、`[flash]`、`[mem]`、
`[symbol]`——而不是默默返回 None。

## 代码结构

```
src/ude_mcp/
  bridge_server.py  本机 HTTP 命令代理 <-> UDE 内 JScript agent
  session.py        UDE 进程 + bridge agent 生命周期
  core.py           高层 API：台架仪式（connect/flash/read）
  symbols.py        GCC/HighTec .map 解析——符号->地址/宽度，离线可用
  config.py         配置文件/环境变量发现
  cli.py            命令行界面
  server.py         MCP server（core 上的薄封装）
tests/test_offline.py  不依赖 UDE 的自检（CI 跑这个）
scratch/              台架历史：产出上面验证证据的脚本
```


## 许可证

[MIT](LICENSE)

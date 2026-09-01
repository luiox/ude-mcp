# API 选型评估：UDE Object Model（COM）vs ".NET API"

> 结论先行：**UDE 没有独立的 .NET API。** PLS 官方自动化接口只有一套 ——
> **UDE® Object Model（COM 自动化接口）**，Python 通过 pywin32/comtypes 直接调用即可，
> 不需要 .NET SDK。所谓 "Ude.Net.dll" 在 UDE Starterkit 2021 及官方资料中均不存在。

## 一、事实核查（针对常见误导）

| 流传说法 | 实际情况 |
| --- | --- |
| "UDE 提供两种 API，务必选 .NET Assembly (Ude.Net.dll)" | 安装目录与官方资料均无 `Ude.Net.dll`。只有 `ude.tlb`（COM 类型库，1094 个类型）和 `UDENetCOMLib.*.dll`（**.NET 侧的 COM 互操作辅助程序集**，本质还是走 COM）。MathWorks 官方集成页明确写 "open software API based on the Microsoft Component Object Model (COM)" |
| "COM 事件回调在 Python 里极难伺候" | MCP 场景**不需要事件回调**。用 `WaitForHalt(timeout)` / `Running()` / `State()` 轮询式阻塞调用即可，comtypes 也支持连接点（真需要时） |
| "UDE API 必须管理员权限" | 硬件（JTAG/DAP）访问可能涉及驱动权限，但 **TSIM 模拟器不需要**。且 stdio MCP server 里用 `ShellExecuteW` 提权重启是错的——新进程脱离了客户端的 stdio 管道，MCP 协议直接断。正确做法：需要提权时由客户端（MCP 配置）整体提权，或把报错信息写清楚 |
| "UDE 必须先加载 .cfg 才能附着，MCP 要设计 load_config + connect 两步" | 方向正确。实际对象模型是：打开工作区（.wsx，内含目标配置）→ `WaitForTargetConnected(timeout)` → 拿 `CoreDebugger(i)`。`.cfg` 是工作区引用的目标配置文件 |

## 二、官方对象模型（已在 ude.tlb 中逐个核实）

```
UDESTK.ApplicationRoot.<ver>   (COM 根对象, IUDEApplication)
  └─ Workspace                 (IUDEWorkspace)
       ├─ CoreDebugger(i)      (IUDEDebuggerInterface, 85 个方法)
       │    ├─ Go/Break/StepIn/StepOver/StepOut/ResetTarget
       │    ├─ Running/State/WaitForHalt/Location/BreakEvent
       │    ├─ ReadVariable/WriteVariable, ReadRegister/WriteRegister
       │    ├─ ReadMemory8/16/32, WriteMemory8/16/32, ReadArray/WriteArray
       │    ├─ Breakpoints     (IUDEBreakpoints: 代码/数据断点, BreakpointHit)
       │    ├─ CallStack, Symbols, Expression, DisASMObj
       │    └─ LoadAndFlash / LoadProgramFile (flash 下载)
       ├─ WaitForTargetConnected / WaitForAllCoresRunning / WaitForAllCoresHalted
       └─ Memtool / TargetManager / MessageLog
```

官方 Python 示例（UDE 手册 "Script-Example for TriCore in Python"）就是用
`win32com.client.Dispatch("UDELauncher")` 起实例、拿 Workspace、下断点、读变量的。
完整对象模型文档在 UDE 帮助系统的 "UDE Automation Guide and Object Model Reference"
（Starterkit 精简版可能未附带该 chm，以类型库逆向为准）。

## 三、三条实现路线对比

### 路线 A：Python + pywin32/comtypes 直连 COM（推荐）
- ✅ 零额外运行时依赖（Python 直调 COM，不需要 .NET SDK / .NET Framework 版本匹配）
- ✅ 官方支持路径，手册有 Python 示例；类型库可自描述（可用 comtypes 从 ude.tlb 生成强类型包装）
- ✅ 64 位进程直连 64 位 COM 服务器（本机 UdeFrameworkManager.dll 为 x64，实测创建成功）
- ⚠️ 个别方法在 in-proc 调用时表现异常，建议按官方姿势走 out-of-proc（launcher 起独立 UDE 进程），
  这也正好保证 MCP 崩溃不连带调试器
- ⚠️ 需要处理 UDE Starterkit 安装器未注册 `UDELauncher` ProgID 的安装缺陷（一次性修复，可写进安装文档）

### 路线 B：pythonnet + UDENetCOMLib.UDELib（.NET interop 包装）
- ✅ 如果将来拿到 PLS 提供的 .NET 层封装，CLR 类型系统对结构体/事件更友好
- ❌ 底层仍是 COM interop（RCW），没有绕开任何 COM 复杂度，反而引入 CLR 运行时版本匹配问题
- ❌ 多一个 pythonnet 依赖（pythonnet 在 .NET Framework 模式下免 SDK，但调试体验一般）
- ❌ 需要装 SDK 的前提不存在：没有官方托管 API 需要编译对接

### 路线 C：绕开 COM——命令行 + 宏脚本（备选/兜底）
- UDE 支持 `UDE.exe -p<workspace> -s<script> -d<logfile>` 无头启动 + 启动宏脚本
- 脚本走 UDE 命令解释器（cmdwin 命令集），适合纯"下载-运行-抓日志"场景
- ❌ 交互式调试（断点命中后读变量再决定下一步）不适合脚本批处理，AI 交互场景必须 COM

## 四、推荐架构

```
┌─────────────┐  stdio (MCP)   ┌──────────────────┐  COM   ┌─────────────────┐
│  AI Client   │ <-----------> │  ude-mcp (Python) │ <----> │ UDE VisualPlatform│
└─────────────┘                │  会话管理/缓存     │        │ (独立进程, 可隐藏) │
                               └──────────────────┘        └─────────────────┘
```

- MCP server 常驻；首次 `open_workspace` 时经 launcher 启动独立 UDE 进程并持有其 COM 对象
- 目标优先选 **TSIM 模拟器**目标配置（安装目录 Targets.dat 已确认 TSIM 目标接口存在），
  无硬件即可跑通全部 MCP 工具链，之后换真机只改 .wsx/.cfg
- 会话状态（当前 workspace / CoreDebugger 句柄 / 符号缓存）在 server 内持有，
  MCP 工具保持无状态签名（路径、表达式作为参数传入）

## 五、风险与未决项

1. UDE Starterkit 免费版的授权（license）是否允许第三方进程全程自动化 —— 需在 TSIM 上实测
2. `UDELauncher` ProgID 缺注册的修复方式：一次性以管理员身份
   `regasm /codebase "...\UDENetCOMLib.UDELauncher.dll"`，或用 UDE 完整版安装包修复注册
3. AURIX(TC3xx) 无 TSIM 模拟目标（TSIM 覆盖 TC179x/TC1.6 系列），实机联调需要 DAP/JTAG 探头
4. 多核（TC364 3 核）操作要显式按 CoreDebugger 索引路由，MCP 工具需要 core 参数

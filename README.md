# Minimal Agent 设计与渐进实现方案

## 1. 目标

构建一个 Windows 优先、Python 实现的极简 Agent，并从最小可运行闭环逐步增加能力。

首要目标：

- API Key + 固定 API 地址 + 固定模型
- HTTP/SSE 流式响应
- 单 Agent，不设计多 Provider、多 Model、多 Agent 枚举
- 模块边界清楚，后续可以独立丰富各模块
- 支持 Session、固定压缩、Builtin Tools、MCP、ACP、Skills 和 Hooks

当前明确不做：

- 不做操作系统级沙箱
- 不做动态 Python 插件扫描
- 不做多模型路由
- 不做 MCP 的 Resources、Prompts、Sampling 等完整能力；第一阶段只接 Tools
- 不在最初版本实现全部模块

> 当前没有沙箱。所有 Tool、Hook 和 MCP 子进程均以启动 Agent 的 Windows 用户权限运行。应用层权限管理不是操作系统安全隔离。

## 2. 技术栈

- Python 3.10 及以上；当前开发基线固定为本机 `C:\Python310\python.exe`（Python 3.10.4，64 位）
- `asyncio`：统一异步运行时
- `httpx` + `httpx-sse`：模型 API 和 SSE
- Python 标准库 `sqlite3`：Session 持久化
- Python 标准库 `dataclasses`、`pathlib`、`logging`
- ACP：官方 `agent-client-protocol>=0.11,<1`
- MCP：官方 Python SDK `mcp>=1.27,<2`

不引入 LangChain、AutoGen、FastAPI、SQLAlchemy 等框架，除非后续出现明确需求。

本机同时存在 Python 3.9 和 32 位 Python 3.9，但本项目不使用它们。ACP Python SDK 和稳定版 MCP Python SDK 的最低要求均为 Python 3.10；项目代码也可以使用 Python 3.10 已支持的 `X | None` 类型标注。创建虚拟环境时显式指定：

```powershell
py -3.10 -m venv tmp\venv
.\tmp\venv\Scripts\python.exe -m pip install --upgrade pip
.\tmp\venv\Scripts\python.exe -m pip install -e ".[dev]"
```

后续如果引入仅在 Python 3.11+ 提供的标准库功能，应优先寻找兼容实现或小型依赖，不因非必要特性升级本机 Python 基线。

## 3. 最终模块结构

```text
src/mini_agent/
  app/
    main.py

  core/
    application.py
    runtime.py
    types.py
    contracts.py

  model_api/
    client.py
    retry.py
    sse.py

  commands/
    parser.py
    dispatcher.py

  context/
    builder.py
    system_rules.py
    token_budget.py
    compression.py

  session/
    migrations.py
    models.py
    store.py

  turns/
    models.py
    manager.py
    cancellation.py

  skills/
    models.py
    loader.py
    registry.py

  tools/
    types.py
    registry.py
    executor.py
    permissions.py

    builtin/
      list_directory.py
      read_file.py
      search_text.py
      write_file.py
      apply_patch.py
      shell/
        powershell.py

    providers/
      mcp/
        client.py
        adapter.py
        config.py

  hooks/
    types.py
    dispatcher.py
    subprocess.py

  protocols/
    acp/
      server.py
      agent.py
      permissions.py
```

不要在项目初始化时创建所有空目录。每个阶段只添加当前真正实现的模块。

## 4. 模块关系

```text
CLI -----------------------> AgentApplication
ACP Adapter ---------------> AgentApplication

AgentApplication ----------> Agent Runtime
AgentApplication ----------> Session Store
AgentApplication ----------> Skill Registry

Agent Runtime -------------> Context Builder
Agent Runtime -------------> Fixed Model Client
Agent Runtime -------------> Tool Registry
Agent Runtime -------------> Hook Dispatcher

Context Builder -----------> Fixed Compression
Tool Registry -------------> Builtin Tools
Tool Registry -------------> MCP Tool Provider
Tool Executor -------------> Permission Manager
```

依赖规则：

1. `app/main.py` 是组装入口，可以导入所有具体实现，但不写业务逻辑。
2. `core` 只使用中立类型和接口，不接触原始 API、ACP、MCP SDK 类型。
3. ACP 是输入适配器，只调用 `AgentApplication`。
4. MCP 是 Tool Provider，转换成内部 `ToolSpec` 后注册到 `ToolRegistry`。
5. Session 不调用 Compression；Core 负责读取历史、调用压缩、保存 checkpoint。
6. Skills 不依赖 Tools；Skill 只声明必需工具或能力，由 Core 检查和绑定。
7. Hooks 由 Core 在固定生命周期点调度，其他模块不主动导入 Hooks。
8. Context 负责组装模型请求，不负责持久化、Tool 执行或协议通信。

## 5. Core 与内部事件

模型 API 的原始 JSON 不进入 Core。`model_api` 将其转换成内部事件：

```python
@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool


@dataclass
class TurnCompleted:
    usage: dict | None = None
```

统一运行入口：

```python
async for event in application.run_turn(session_id, prompt):
    ...
```

CLI、Session、ACP 和测试都消费同一套内部事件。

## 6. Context 与 System 规则

Context 表示“当前这一轮真正发送给模型的内容”。它由以下内容构成：

```text
内置基础 System 规则
+ 用户全局规则
+ 工程规则
+ Session 规则
+ Hook 附加上下文
+ 激活的 Skill 指令
+ 压缩摘要
+ 最近原始历史
+ 当前用户 Prompt
+ 可用 Tool 定义
```

规则数据跟随对应生命周期：

- 全局规则：`%LOCALAPPDATA%\MiniAgent\system.md`
- 工程规则：`<project>\AGENTS.md` 或 `<project>\.mini-agent\system.md`
- Session 规则：保存在 Session 元数据中

`session` 保存 `project_root`、Session 规则和规则 hash；`context/system_rules.py` 负责读取、解析和排列。Session Store 不读取工程文件。

System Prompt 只能引导模型，不能作为权限安全边界。文件范围、Shell、网络和写操作限制必须由 Tool 权限链路强制执行。

## 7. Session 与 Compression

Session 使用 SQLite 保存：

- Session 元数据
- User/Assistant 消息
- Tool Call/Tool Result
- 错误和中断状态
- 激活的 Skill
- 压缩 checkpoint

Compression 不访问 SQLite。它只接收事件并返回结果：

```python
@dataclass
class CompressionResult:
    summary: str
    through_seq: int
    version: str
```

由 Core 编排：

```python
events = await session_store.load_events(session_id)
result = await compressor.compress(events)
await session_store.save_checkpoint(session_id, result)
```

固定压缩规则：

- 上下文达到窗口约 75% 时触发
- 压缩最早的完整 Turn，目标降到约 50%
- Tool Call 与对应 Tool Result 不拆分
- 原始事件不删除
- 压缩 Prompt 和结果带版本

## 8. Skills

Skill 第一版只包含：

- `SKILL.md`
- references
- templates
- 必需工具声明

Skill 不直接调用 Tools，不操作 Session，不执行 Python 脚本，也不能绕过权限。

示例：

```yaml
---
name: code-review
required_tools:
  - read_file
  - search_text
---
```

Core 负责：

```python
skill = skill_registry.load("code-review")
tool_registry.ensure_available(skill.required_tools)
```

第一版只支持显式激活，后续再增加自动匹配。

## 9. Tools：Builtin 与 Provider

Tools 按来源分为：

- `builtin`：随 Agent 发布、由本项目维护
- `providers`：后续接入的 Tool 来源，第一种是 MCP

所有 Tool 进入同一个 Registry、权限和执行链路：

```text
Builtin ----\
MCP ---------> ToolRegistry -> ToolExecutor
Local ------/
```

统一描述：

```python
@dataclass
class ToolSpec:
    id: str
    name: str
    description: str
    input_schema: dict
    source: str
    capabilities: set[str]
    risk: str
```

Builtin 不等于自动授权。例如 `builtin.shell_exec` 仍然是最高风险工具。

Windows 下统一使用 `shell_exec`，默认实现为：

```text
powershell.exe -NoLogo -NoProfile -NonInteractive
```

不把 Tool 命名为 Bash。以后可以增加 `pwsh`、Git Bash 或 WSL Provider。

## 10. Tool 权限设计

### 10.1 当前安全边界

当前 Agent 不使用 OS 沙箱，因此权限系统只能：

- 决定 Agent 是否调用某个 Tool
- 限制受控文件 Tool 的路径
- 在执行高风险操作前要求用户确认
- 限制超时、输出大小和传给子进程的环境变量

它不能阻止已获准的 Shell 命令访问当前 Windows 用户能够访问的其他路径或网络。

### 10.2 Tool 调用链路

```text
Model Tool Call
  -> Schema 校验与参数规范化
  -> Hard Guard 初次检查
  -> PreToolUse Hook（只能阻断或重写）
  -> 重写后再次 Schema 校验和 Hard Guard
  -> PermissionManager：ALLOW / ASK / DENY
  -> PermissionBroker：必要时询问用户
  -> ToolExecutor
  -> PostToolUse / PostToolUseFailure
```

必须在 Hook 重写参数后重新检查权限，用户确认的必须是最终真正执行的参数。

### 10.3 权限结果

```python
class PermissionDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
```

第一版只支持：

- 允许本次
- 拒绝

后续再增加：

- 当前 Session 允许
- 当前工程允许

### 10.4 默认权限

| Tool | 默认策略 |
|---|---|
| `list_directory` | 工作区内允许 |
| `read_file` | 工作区内允许 |
| `search_text` | 工作区内允许 |
| `write_file` | 询问 |
| `apply_patch` | 询问 |
| `shell_exec` | 默认禁用或每次询问 |
| MCP 只读 Tool | 首次或每次询问，由配置决定 |
| MCP 写入/破坏性 Tool | 每次询问 |

文件 Tool 必须解析真实路径并验证仍位于 `workspace_root` 下，Windows 还需考虑 junction、symlink 和 reparse point。

Shell 是绕过受控文件 Tool 的逃生口。初始 Tool 闭环不启用 Shell；增加 Shell 后默认每次确认，并明确提示其以当前 Windows 用户权限运行。

### 10.5 PermissionBroker

权限交互通过接口解耦：

```python
class PermissionBroker(Protocol):
    async def request(
        self,
        request: PermissionRequest,
    ) -> PermissionDecision:
        ...
```

- 初期：`CliPermissionBroker`
- ACP 阶段：`AcpPermissionBroker`

PermissionManager 负责政策，PermissionBroker 负责和用户交互。

## 11. 权限与 Hooks 的关系

Hooks 不是基础权限系统。

本 Agent 中：

- Hard Guard 和 PermissionManager 是每次 Tool 调用必经链路
- Hooks 可以进一步阻止、审计或重写
- Hooks 不能把 `DENY` 改成 `ALLOW`
- 禁用 Hooks 后，基本权限仍然有效
- Hook 本身是外部代码时，也需要信任和执行限制

这与 Codex 当前公开机制的方向一致：Codex 本地权限的基础是 OS 级 Sandbox 和 Approval Policy；Hooks 是额外的生命周期扩展，可用于 `PreToolUse`、`PermissionRequest`、审计和组织策略，但不是读写执行权限的唯一来源。

参考：

- [Codex Agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [Codex Hooks](https://learn.chatgpt.com/docs/hooks)

## 12. 渐进实施阶段

### 阶段 0：工程与单轮 SSE

- 创建 `app`、`core`、`model_api`
- 固定模型和 API 地址
- API Key 从环境变量读取
- CLI 输入一句话，SSE 流式输出

验收：能够稳定完成一轮纯文本对话。

### 阶段 1：内部事件与内存多轮

- 增加 `TextDelta`、`ToolCall`、`TurnCompleted`
- Runtime 使用异步事件流
- 增加 Fake Model 测试

验收：多轮历史在内存中工作，API JSON 不泄漏到 Core。

### 阶段 2：只读 Builtin Tools

- `list_directory`
- `read_file`
- `search_text`
- Tool Registry、Executor、基础权限

验收：模型能够读取工作区内容并继续回答，不能通过文件 Tool 访问工作区外路径。

### 阶段 3：写 Tool 与权限确认

- `write_file`
- `apply_patch`
- `CliPermissionBroker`

验收：写操作必须展示最终参数并取得本次授权。

### 阶段 4：Session

- SQLite Event Store
- create/list/resume/archive/delete
- 保存消息与 Tool Call/Result

验收：程序重启后能够恢复完整会话。

### 阶段 5：Context 与规则

- 全局、工程、Session 规则
- Context Builder
- Token Budget

验收：不同工程和 Session 使用不同规则来源。

### 阶段 6：固定压缩

- 固定阈值和摘要格式
- 保存 checkpoint
- 原始事件不删除

验收：长会话可以压缩和恢复，Tool Call/Result 不被拆分。

### 阶段 7：Skills

- 显式激活
- 加载 `SKILL.md`
- 必需 Tool 检查
- Session 保存 Skill 名称和 hash

验收：Skill 指令能够进入 Context，Skill 不直接依赖 Tools。

### 阶段 8：Hooks

第一批：

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUseFailure`
- `Stop`

验收：Hooks 可以收紧策略，但不能绕过 PermissionManager。

### 阶段 9：MCP Tool Provider

- 先支持 stdio
- 只支持 `tools/list` 和 `tools/call`
- MCP Tool 适配成内部 `ToolSpec`
- 使用统一权限和 Hooks

验收：Agent Core 不接触 MCP SDK 类型。

### 阶段 10：ACP

- ACP 作为 AgentApplication 的输入适配器
- 映射 Session 生命周期
- 转换内部增量事件
- 使用 `AcpPermissionBroker`

验收：CLI 和 ACP 使用同一个 AgentApplication，不复制业务逻辑。

### 阶段 11：Shell

- Windows PowerShell 实现
- 每次确认
- 超时和输出限制
- 敏感环境变量过滤

Shell 放在后期，是因为当前没有沙箱，它会显著扩大风险边界。

## 13. 实施纪律

1. 每个阶段都必须独立可运行、可测试、可验收。
2. 不提前创建无实现的空模块。
3. 不因未来可能扩展而建立 AnyClient/AnyModel/AnyAgent 层级。
4. 所有协议适配器转换成内部类型后才能进入 Core。
5. 所有 Tool 不论来源都经过统一 Registry、Guard、Permission、Hook 和 Executor。
6. Hooks 不是权限系统，也不是沙箱。
7. System Prompt 不是安全边界。
8. 没有沙箱时，Shell、外部 Hook 和 MCP Server 都按当前用户完整权限看待。
9. 优先实现只读闭环，再增加写入和执行能力。
10. 只有出现真实问题时才增加抽象和依赖。

## 14. 当前实现状态

文档中的阶段 0–11 均已实现。当前 `v0.2.0` 版本包括：

- 固定 `qwen3.7-plus` 与固定 DashScope Coding OpenAI 兼容地址
- OpenAI Chat Completions SSE 增量解析、Tool Call 拼接、用量统计和流中断检测
- 中立内部事件、模型/工具多轮循环和 CLI
- 工作区只读/写入 Builtin Tools，以及默认关闭的 PowerShell Tool
- SQLite Session 创建、恢复、归档、删除、事件和压缩 checkpoint
- 固定 75% 触发、约 50% 目标的版本化压缩
- 全局、工程、Session System 规则，以及显式 Skill 激活
- 命令型 Hooks：`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`Stop`
- MCP stdio `tools/list`、`tools/call` Provider
- ACP Session、Prompt、增量事件、取消和权限适配
- 同一 Session 的 Turn FIFO、不同 Session 并发、取消与重启中断恢复
- 模型请求在首个可见输出前的安全重试和 Model Attempt 记录
- CLI/ACP 共用 `/new`、`/list`、`/resume`、`/zip` 会话命令
- 分阶段会话压缩、原子文件写入、预期 SHA-256 和 MCP 启动隔离

实现仍遵守最初的安全边界：没有 OS 沙箱；Shell、Hook 和 MCP 子进程都拥有启动 Agent 的 Windows 用户权限。

## 15. 安装与运行

在本目录执行：

```powershell
C:\Python310\python.exe -m venv tmp\venv
.\tmp\venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:MINI_AGENT_API_KEY = "你的 DashScope Coding API Key"
.\tmp\venv\Scripts\mini-agent.exe --workspace D:\path\to\project chat
```

交互会话支持：

```text
/new [name]                 新建并切换 Session
/list                       列出当前工作区 Session
/resume <session-id|name>   按完整 ID、唯一 ID 前缀或名称恢复
/zip                        主动压缩完整历史 Turn
```

也可以使用 `DASHSCOPE_API_KEY`。API Key 只从环境变量读取，不写入配置、Session 或日志。数据默认保存在 `%LOCALAPPDATA%\MiniAgent`；可用 `MINI_AGENT_DATA_DIR` 或 `--data-dir` 覆盖。

Session 管理不需要 API Key：

```powershell
.\tmp\venv\Scripts\mini-agent.exe session create
.\tmp\venv\Scripts\mini-agent.exe session list
.\tmp\venv\Scripts\mini-agent.exe session archive <session-id>
.\tmp\venv\Scripts\mini-agent.exe session resume <session-id>
.\tmp\venv\Scripts\mini-agent.exe session delete <session-id>
```

PowerShell Tool 默认不注册。仅在明确接受无沙箱风险时启用：

```powershell
.\tmp\venv\Scripts\mini-agent.exe --enable-shell chat
```

ACP stdio 入口：

```powershell
.\tmp\venv\Scripts\mini-agent-acp.exe
```

## 16. 配置

### 16.1 MCP

全局配置位于 `%LOCALAPPDATA%\MiniAgent\mcp.json`，工程配置位于 `<project>\.mini-agent\mcp.json`；工程内同名 Server 覆盖全局配置。

```json
{
  "mcpServers": {
    "example": {
      "command": "python",
      "args": ["path/to/server.py"],
      "cwd": ".",
      "env": {"EXAMPLE_MODE": "local"},
      "enabled": true
    }
  }
}
```

MCP Tool 暴露名为 `mcp__<server>__<tool>`。MCP 注解只用于风险提示，所有 MCP Tool 仍经过统一 Schema、Hook、权限和 Executor 链路。外部 Server 的写入、网络和破坏性 Tool 不缓存授权。

### 16.2 Skills

全局 Skill 放在 `%LOCALAPPDATA%\MiniAgent\skills\<name>\SKILL.md`，工程 Skill 放在 `<project>\.mini-agent\skills\<name>\SKILL.md`：

```yaml
---
name: code-review
description: Review local code
required_tools: [read_file, search_text]
---
Read the relevant files first and report findings with paths.
```

通过 CLI 的 `/skill code-review` 显式激活，`/unskill code-review` 取消激活。Skill 资源只能从自身目录读取。

### 16.3 Hooks

全局 Hook 位于 `%LOCALAPPDATA%\MiniAgent\hooks.json`，工程 Hook 位于 `<project>\.mini-agent\hooks.json`：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "write_file|apply_patch",
        "hooks": [
          {
            "command_windows": ["python", "scripts/policy_hook.py"],
            "timeout": 30,
            "required": true
          }
        ]
      }
    ]
  }
}
```

Hook 从 stdin 接收 JSON，只能返回继续、阻断、附加 Context 或重写参数。重写后的参数会再次执行 Schema 和工作区 Hard Guard，再进入权限确认。敏感环境变量不会传给 Hook 子进程。

## 17. 验证

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONPYCACHEPREFIX = "tmp\pycache"
.\tmp\venv\Scripts\python.exe -m pytest
.\tmp\venv\Scripts\python.exe -m pip check
.\tmp\venv\Scripts\python.exe -m compileall -q src tests
```

测试运行产物由 Pytest 固定写入 `tmp\pytest`。当前测试覆盖 SSE、固定配置、权限作用域、路径 Hard Guard、写入与 Shell、Session、规则、压缩原子性、Skills、Hooks、真实 MCP stdio 生命周期和 ACP 映射。

## 18. 个人微信 ACP 接入

项目提供 [scripts/start-weixin-acp.ps1](scripts/start-weixin-acp.ps1)，用于在可信的单用户 Windows 服务器上通过固定版本 `weixin-acp@0.6.0` 启动当前 ACP Agent。

该入口显式设置：

```text
MINI_AGENT_PERMISSION_MODE=trusted
MINI_AGENT_ENABLE_SHELL=1
```

`trusted` 会由 Agent 自身直接允许所有已注册 Tool，不依赖 ACP Client 自动选择权限选项。默认 CLI 和未设置该变量的 ACP 部署仍使用 `standard`。可用权限模式为：

| 模式 | 行为 |
|---|---|
| `standard` | Builtin 只读自动允许，其余请求 PermissionBroker |
| `trusted` | 所有已注册 Tool 直接允许 |
| `locked` | 只允许 Builtin 只读 Tool，其余拒绝 |

先进行不会下载包、不会登录微信的环境检查：

```powershell
.\scripts\start-weixin-acp.ps1 `
  -Workspace D:\path\to\workspace `
  -DataDir D:\MiniAgent\data `
  -ValidateOnly
```

首次扫码并在登录后直接启动：

```powershell
$env:MINI_AGENT_API_KEY = "你的 API Key"
.\scripts\start-weixin-acp.ps1 `
  -Workspace D:\path\to\workspace `
  -DataDir D:\MiniAgent\data `
  -Login
```

如果不希望把 API Key 预先写入环境变量，可以在可见 PowerShell 窗口中交互输入：

```powershell
.\scripts\start-weixin-acp.ps1 `
  -Workspace D:\path\to\workspace `
  -DataDir D:\MiniAgent\data `
  -Login `
  -PromptForApiKey
```

输入内容不会回显，仅存在于当前启动进程及其 ACP 子进程环境中，进程结束后清除。

后续启动时省略 `-Login`。如需暂时关闭 PowerShell Tool，增加 `-DisableShell`。

脚本要求 Node.js 22 及以上，验证 ACP Agent 可执行文件，并将 npx 下载缓存固定到 `tmp\npm-cache`。API Key 不会作为命令行参数输出；它通过进程环境传给 ACP Agent。

当前微信 ACP 接入边界：

- 当前 Mini Agent ACP Adapter 只接受文本 Prompt；图片、语音和文件尚未接入。
- `weixin-acp` 重启后会为微信会话创建新的 ACP Session；Mini Agent 的旧 Session 仍保存在 SQLite，但桥接层暂不自动恢复映射。
- `trusted` 加上 PowerShell 意味着 Agent 拥有运行它的 Windows 用户权限；建议使用专用的非管理员 Windows 用户。
- `weixin-acp` 是非微信官方项目。扫码登录前应理解其登录令牌和账号风险；本项目不会使用已禁用的 `wx-cli` 或读取微信本地数据库。

## 19. 版本规划

当前代码版本为 `v0.2.0`，在 `v0.1.0` 最小闭环之上完成可靠 Turn、会话控制、安全模型重试、可恢复压缩和 Tool/MCP 可靠性加固。本版本不实现工作区切换，也不扩展 ACP 多媒体或完整 MCP 能力。详细范围、命令语义和验收条件见 [docs/ROADMAP.md](docs/ROADMAP.md)。

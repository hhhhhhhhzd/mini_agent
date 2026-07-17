# Mini Agent Roadmap

## v0.1.0：最小可运行闭环

`v0.1.0` 是第一个可运行基线，目标是证明各模块能够通过统一应用层组成完整 Agent，而不是提供生产级可靠性。

已经具备：

- 固定 DashScope API 地址与固定模型，使用 SSE 流式传输。
- 单 Agent Tool Loop，以及文件读取、检索、写入、补丁和可选 PowerShell Tool。
- `standard`、`trusted`、`locked` 三种应用层权限模式。
- SQLite Session 创建、恢复、归档、删除和压缩检查点。
- System Rules、Skills、Hooks、MCP Tools 和 ACP 文本协议。
- Windows 优先的 CLI，以及 `weixin-acp` 启动脚本。

已知边界：

- 没有操作系统级沙箱；PowerShell 以当前 Windows 用户权限运行。
- 模型请求没有瞬时错误重试和退避。
- 同一 Session 的并发 Turn 没有统一协调。
- 运行中断和进程崩溃没有完整的 Turn 恢复状态。
- ACP 只支持文本，不支持图片、语音、文件和 Embedded Context。
- MCP 当前只接入 stdio Tools，不包含 HTTP、Resources 和 Prompts。

## v0.2.0：可靠运行层

`v0.2.0` 的主题是“同一个 Turn 可以被识别、串行、取消、记录和恢复”。该版本优先加固现有闭环，不扩展多模型或多 Agent。

### 1. Runs 模块

新增 `runs/`，统一管理 CLI、ACP 和后续消息通道的运行生命周期：

```text
runs/
  models.py        # run_id、session_id、状态、时间和错误分类
  manager.py       # 每 Session 串行控制和活动任务注册
  cancellation.py  # 统一取消令牌与子进程终止
  retry.py         # 可复用的瞬时错误重试策略
```

基础状态：

```text
queued -> running -> waiting_tool -> completed
                   |-> failed
                   |-> cancelled
                   `-> interrupted
```

### 2. Model API 可靠性

- 对连接失败、429、502、503 和 504 实施有上限的指数退避。
- 尊重 `Retry-After`，记录服务端请求 ID 和错误类别。
- 仅在响应尚未产生可见输出或 Tool Call 时自动重试，避免重复执行工具。
- 明确区分可重试错误、永久错误和用户取消。

### 3. Session 与数据库迁移

- 增加 Run/Turn 持久化结构和数据库 schema version。
- 提供从 `v0.1.0` 数据库平滑升级的迁移测试。
- Agent 启动时把遗留的 `running` 状态修正为 `interrupted`。
- 同一 Session 的 Prompt 必须串行处理或明确拒绝，不允许历史交错写入。

### 4. Tools 与结果安全

- 文件写入和补丁使用原子替换，避免进程退出后留下半文件。
- 写入操作支持预期哈希，检测并发修改。
- 长工具输出保存为 Artifact，模型上下文只保留摘要、状态和引用路径。
- CLI、ACP 和 MCP 取消都能向正在运行的 Tool 传播。

### 5. Context 与压缩

- 支持分块或递归压缩，避免待压缩输入本身超过上下文。
- 压缩失败时保留最近历史继续运行，并生成可诊断事件。
- 保存压缩前后 token 估算、覆盖区间、版本和失败原因。

### 6. Skills、Hooks、MCP 与 ACP 加固

- Skills 使用完整 YAML Front Matter，并提供受限的 Skill Resource 读取能力。
- 增加 `TurnStart`、`PreCompact`、`PostCompact` 和 `SessionEnd` Hook。
- 明确多个 Hook 重写参数时的顺序和冲突规则。
- 单个 MCP Server 故障不再阻止 Agent 启动，并增加健康状态和重连。
- ACP 与 CLI 通过 Runs 模块共享取消和运行状态，不再各自维护任务表。

### 7. v0.2.0 验收条件

- 同一 Session 同时提交两个 Prompt 时，历史不会交错。
- PowerShell 或 MCP Tool 执行期间可以取消，并且不会留下子进程。
- 模型在首次输出前返回 429/503 时能够按策略恢复。
- 模型已经输出 Tool Call 后发生断流时，不会自动重复执行该 Tool。
- Agent 在 Turn 中强制退出后，重新启动能识别并记录中断状态。
- `v0.1.0` 创建的数据库能够自动迁移并正常恢复 Session。
- 压缩、重试、取消、迁移和并发行为都有自动化测试。

## v0.2.0 不包含

- 操作系统级沙箱或 Windows 容器隔离。
- 多 Provider、多 Model 路由和多 Agent 编排。
- 后台定时任务、长期记忆和向量数据库。
- 微信图片、语音和文件处理；这些能力计划在可靠运行层稳定后进入后续版本。

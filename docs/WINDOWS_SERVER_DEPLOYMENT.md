# Windows Server 部署

目标安装目录：

```text
C:\Users\Administrator\Desktop\agent\MiniAgent
```

服务器只需要预先安装 Git。安装脚本会把 Python、Node.js、虚拟环境、数据和工作区全部放在 MiniAgent 目录内，不修改系统级 PATH。

## 首次安装

在服务器 PowerShell 中执行：

```powershell
New-Item -ItemType Directory -Force C:\Users\Administrator\Desktop\agent | Out-Null

git clone https://github.com/hhhhhhhhzd/mini_agent.git `
  C:\Users\Administrator\Desktop\agent\MiniAgent

git -C C:\Users\Administrator\Desktop\agent\MiniAgent fetch origin `
  refs/heads/v0.2.0:refs/remotes/origin/v0.2.0

git -C C:\Users\Administrator\Desktop\agent\MiniAgent checkout -B v0.2.0 `
  refs/remotes/origin/v0.2.0

powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\install-windows-server.ps1
```

安装内容：

```text
MiniAgent\runtime\python    Python 3.10.11 私有运行时
MiniAgent\runtime\node      最新 Node.js 22 LTS 便携运行时
MiniAgent\tmp\venv          Mini Agent Python 虚拟环境
MiniAgent\data              Session、SQLite、Hooks、Skills、MCP 配置
MiniAgent\workspace         Agent 默认工作区
```

## 启动 CLI

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-cli.ps1
```

首次安装会生成根目录 `config.json`（已有文件不会覆盖）。启动前填写 `model.api_key`，模型和接口地址也在该文件配置；不再交互输入 Key。

指定其他工作区：

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-cli.ps1 `
  -Workspace C:\Users\Administrator\Desktop\gerlun
```

## 首次启动微信

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-weixin.ps1 `
  -Login
```

扫码登录完成后，后续启动省略 `-Login`：

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-weixin.ps1
```

使用 `gerlun` 作为微信 Agent 工作区：

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-weixin.ps1 `
  -Workspace C:\Users\Administrator\Desktop\gerlun
```

微信接入只有在该进程持续运行时才会回复消息。前台运行时优先在原窗口按 `Ctrl+C` 停止；如果窗口已经丢失或进程无响应，可执行：

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-stop.ps1 `
  -Mode weixin
```

`-Mode cli` 只停止 CLI，`-Mode all` 停止该 MiniAgent 安装目录下的 CLI、ACP 和私有 Node.js 进程。

## 只做环境检查

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-cli.ps1 `
  -ValidateOnly

powershell -ExecutionPolicy Bypass -File `
  C:\Users\Administrator\Desktop\agent\MiniAgent\scripts\server-weixin.ps1 `
  -ValidateOnly
```

默认权限模式为 `trusted`，并启用 PowerShell Tool。此模式没有操作系统沙箱，Agent 拥有 Administrator 用户权限。可通过 `-DisableShell` 临时关闭 PowerShell Tool，或者在 CLI 启动时指定 `-PermissionMode standard`。

升级或备份前应先停止 Agent。需要长期保留的是 `MiniAgent\data`、根目录 `config.json` 和 `runtime\launch.json`；不要删除这些文件。

## 一键更新

在服务器独立 PowerShell 窗口执行，不要让运行中的 Agent 执行更新自身的命令：

```powershell
Set-Location C:\Users\Administrator\Desktop\agent\MiniAgent
powershell -ExecutionPolicy Bypass -File .\scripts\server-update.ps1
```

新版 `server-cli.ps1` / `server-weixin.ps1` 会记录最近一次启动的模式、工作区和权限参数到 `runtime\launch.json`。更新后在当前窗口按这些参数重启；窗口需要保持打开。只恢复一种最近使用的模式，不恢复多个实例，也不自动重复登录。

旧部署尚无启动记录时，首次指定模式和原工作区：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\server-update.ps1 `
  -Mode weixin -Workspace C:\Users\Administrator\Desktop\gerlun
```

首次需要先取得更新脚本（成功后再执行下一条）：

```powershell
git switch v0.2.0
git pull --ff-only origin refs/heads/v0.2.0
```

脚本同时记录已安装提交号。首次使用即使代码已最新，也会重新安装以保证 Python 包同步；后续代码与安装记录均为最新时才跳过更新。之后不必提前手动 pull。

可选参数：

- `-CheckOnly`：获取远端信息，检查是否有新版本，不停止或安装。
- `-NoRestart`：完成更新后保持停止状态。
- `-Mode cli` / `-Mode weixin`：指定更新后的启动模式。
- `-Workspace <路径>`：覆盖保存的工作区。

更新当前分支对应的 `origin` 分支，仅允许快进；有本地修改或未忽略文件时停止，不自动覆盖。下载和构建安装包在停止 Agent 之前完成，之后备份、离线安装并检查依赖、配置与入口。备份保存在 `runtime\updates\时间编号`，包括旧提交号、虚拟环境、配置和会话数据；备份可能包含密钥，按原配置文件同等保管。备份不自动清理。

安装或检查失败时恢复旧代码与虚拟环境，并保持停止，按提示用原启动脚本重新启动。备份失败则不修改代码和环境。更新不会主动运行数据库迁移或覆盖 data。启动后的网络、账号登录、消息收发不属于自动检查范围；自动回退不涵盖重启后才出现的问题。新版本若改变 Python/Node 运行时要求，需要另行升级运行时。

如果使用早期的部署脚本遇到 `egg_base option: 'tmp/build' does not exist`，可先执行以下兼容性修复，再重新运行安装脚本：

```powershell
New-Item -ItemType Directory -Force `
  C:\Users\Administrator\Desktop\agent\MiniAgent\tmp\build | Out-Null
```

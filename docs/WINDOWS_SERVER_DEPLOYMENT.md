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

没有设置 API Key 时，启动器会以隐藏输入方式询问。

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

升级或备份前应先停止 Agent。需要长期保留和备份的是 `MiniAgent\data`；不要删除该目录。

如果使用早期的部署脚本遇到 `egg_base option: 'tmp/build' does not exist`，可先执行以下兼容性修复，再重新运行安装脚本：

```powershell
New-Item -ItemType Directory -Force `
  C:\Users\Administrator\Desktop\agent\MiniAgent\tmp\build | Out-Null
```

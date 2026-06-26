# 开发说明

启动 API：

```powershell
make api
```

启动测试 UI：

```powershell
make web
```

运行测试：

```powershell
make test
```

运行全部测试入口：

```powershell
make test-all
```

`test-all` 会先尝试 Vitest；没有 `package.json` / `vitest.config.*` 时跳过前端测试。后端优先使用 pytest；当前环境没有 pytest 时会自动回退到 unittest。

## CORS

本地 Web 来源在这里允许：

```text
apps/api/src/gateway/main.py
```

默认允许：

```text
http://127.0.0.1:5173
http://localhost:5173
```

中间件还通过 local-only regex 允许其他 localhost / 127.0.0.1 端口。

浏览器控制台里来自插件的错误，例如 `zotero.js`，和本项目无关。只有请求 URL 指向本项目 API 时才需要沿 API 链路排查。

## Codex CLI 配置

Codex 可执行文件和运行配置读取位置：

```text
apps/api/src/gateway/config.py
```

常用环境变量：

```text
CODEX_MAX_RUNTIME_SECONDS=900
GATEWAY_DATA_DIR=data
```

本地和 SSH Codex 设备通过 Devices 面板或 `/api/devices` 配置。设备 executable path 存 SQLite，不写 `.env`。

SSH 设备必须填写远端 `codex_executable` 绝对路径，例如：

```text
/home/openclaw/.local/bin/codex
```

不要依赖远端非交互 shell 的 `PATH`。

密码模式需要：

```powershell
pip install paramiko
```

提交任务前，先在 Devices 面板检查 SSH 和远端 Codex 可用性，或调用：

```text
POST /api/devices/{device_id}/health-check
```

## 命令组装位置

Run 记录和命令参数在这里组装：

```text
apps/api/src/gateway/services/run_service.py
```

实际 subprocess 命令在这里构建和执行：

```text
apps/worker/src/codex_gateway_worker/codex_runner.py
```

当前本地命令形状：

```powershell
codex exec -C <workspace_path> --sandbox workspace-write --skip-git-repo-check --json --output-last-message <final.md> -
```

Prompt 文本通过 stdin 传给 `codex exec`。

## 当前 SSH 模式链路

当前 SSH 模式仍是过渡实现：

1. API 创建本地 session workspace：`data/sessions/{session_id}`。
2. 网关把本次 run 选中的文件 materialize 到 session。
3. Worker 通过 SCP/SFTP 把 session workspace 复制到远端：

```text
<device remote_root>/{session_id}
```

4. 远端执行：

```powershell
codex exec -C <remote_workspace> --sandbox workspace-write --skip-git-repo-check --json --output-last-message <remote_workspace>/.gateway/run_final.md -
```

5. Worker 把远端 workspace 复制回本地 session。
6. API 扫描 `outputs/` 并登记输出资产。

这条链路不再作为长期扩展方向。后续目标是 Codex 机器通过 gateway 授权拿 signed URL，直接从 OSS 下载输入、上传输出。详见：

```text
docs/architecture-data-plane.md
```

## 日志

Runner 会在 Codex 运行时流式读取 stdout/stderr。UI 展示的是平台事件、stdout/stderr、Codex JSONL 事件和最终 assistant message，不展示模型隐藏思维链。

## 精确停止本地服务

如果 API 在前台运行，按：

```text
Ctrl+C
```

如果 API 在后台运行，先只找绑定 `8010` 的进程：

```powershell
Get-NetTCPConnection -LocalPort 8010 | Select-Object LocalAddress,LocalPort,State,OwningProcess
```

再停止对应 pid：

```powershell
Stop-Process -Id <OwningProcess>
```

静态 Web 服务端口 `5173` 同理：

```powershell
Get-NetTCPConnection -LocalPort 5173 | Select-Object LocalAddress,LocalPort,State,OwningProcess
Stop-Process -Id <OwningProcess>
```

不要直接杀掉所有 `python.exe`，这可能会停掉其他工具。

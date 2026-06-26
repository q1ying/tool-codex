# Codex 作用范围限制说明

结论：只靠 prompt 不够。prompt 只能提醒模型遵守规则，不能作为安全边界。

当前项目已经做的硬限制：

1. 每次 run 创建独立 session workspace：

```text
data/sessions/{session_id}/
```

2. Codex 启动时使用 `-C <workspace_path>`，工作根目录不是项目根目录：

```powershell
codex exec -C <workspace_path> --sandbox workspace-write --skip-git-repo-check --json ...
```

3. 平台上传和保存文件时做路径边界检查：

```text
apps/api/src/gateway/services/path_security.py
```

这能防止平台自己把文件写到 workspace 外面，也能让 Codex 默认在指定 workspace 下工作。

但还要注意：

- prompt 不能阻止模型“想要”读取别的路径。
- `-C` 是工作根目录限制，不等于操作系统级权限隔离。
- `--sandbox workspace-write` 比裸跑安全，但如果机器上有敏感文件，仍建议不要把敏感目录放在 Codex 可见的上下文附近。

如果要更强的强制限制，建议按强度从低到高做：

## 方案 A：把 workspace 放到独立目录

不要把 Codex 的 `-C` 指到项目根目录 `D:\简圣科技\codex`，而是只指到：

```text
D:\简圣科技\codex\data\workspaces\user_default\conv_xxx
```

当前实现会让 Codex 从 session workspace 执行，而不是从项目根目录执行。

如果你不希望 Codex 看到项目 `./` 下的任何源码，进一步建议把 `GATEWAY_DATA_DIR` 放到项目外的空目录，例如：

```powershell
$env:GATEWAY_DATA_DIR="D:\codex_gateway_runtime"
```

这样 session workspace 会在：

```text
D:\codex_gateway_runtime\sessions\sess_xxx
```

## 方案 B：容器隔离

每个 job 用一个容器运行 Codex，只挂载当前 workspace：

```text
host workspace -> container /workspace
codex exec -C /workspace --sandbox workspace-write ...
```

容器内不挂载项目源码、不挂载用户主目录、不挂载 `.env`。

## 方案 C：独立系统用户或 ACL

让 Codex CLI 进程用低权限用户运行。该用户只能读写 workspace，不能读项目源码、用户目录、服务器配置。

Windows 上可以用目录 ACL 做到类似效果。

## 方案 D：一次性临时目录

每个 run 都使用临时 session workspace，Codex 跑完后只收集 `outputs/`，过期后清理临时目录。

这对“不要访问当前项目 `./`”最干净。

## 方案 E：OSS signed URL 数据面

长期远程执行不应让主服务器通过 SFTP 搬完整 workspace。更好的做法是：

- Gateway 只下发小型 run manifest 和权限边界。
- Codex 通过 run-scoped 工具请求文件。
- Gateway 校验权限后返回短期 signed URL。
- Codex 直接从 OSS 下载输入、上传输出。

这样对象存储承担数据面，Gateway 仍负责控制面、权限和审计。

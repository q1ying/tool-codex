# TODO

## 当前优先级

远程执行的长期方向已经改为：

- 主服务器：控制面、调度面、权限面。
- OSS：数据面、文件传输面。
- Codex 机器：执行面，按需拉取输入并直接上传结果。

因此，远程 SFTP workspace sync 只作为 MVP 过渡路径保留，不再作为大文件扩展方案继续深挖。新的架构规划见：

```text
docs/architecture-data-plane.md
```

## 下一阶段：OSS signed URL 数据面

已完成基础项：

- `StorageService.presign_upload()`。
- run-scoped 下载授权接口：

```text
POST /api/runs/{run_id}/assets/{asset_id}/download-url
```

- run-scoped 上传授权接口：

```text
POST /api/runs/{run_id}/artifacts/upload-url
```

已完成 artifact 完成登记接口：

```text
POST /api/runs/{run_id}/artifacts/complete
```

它会校验 object_key 作用域、size、sha256，并登记 `assets`、`file_assets`、`run_assets`、`asset_lineage` 和事件。

仍待实现：

1. 更完整的前端展示和下载入口。
2. 如果 SQLite 本地旧数据冲突，直接清理 `data/` 后重跑，不做隐藏兼容。

## 下一阶段：候选资产 manifest

把当前“materialize 文件列表”改成“候选资产和权限 manifest”：

- `FilePlanner` 保留，但输出从 `materialize/reference_only/ignored` 逐步改成 `candidate/required_guidance/deferred/ignored`。
- manifest 中包含 `asset_id`、filename、kind、role、mime、size、sha256、branch、version、summary、why_included、available_derivatives。
- Prompt wording 改成“这些文件可按需访问”，不要再说“文件已经下载到 workspace”。
- `run_assets` 继续作为候选、下载、chunk 读取、输出登记的审计表。

## 下一阶段：Codex 资产工具

基础 HTTP MCP endpoint 已放在：

```text
apps/api/src/gateway/mcp/
```

地址：

```text
http://主服务器IP:8010/mcp
```

本服务器的局域网 IP 通过项目根目录 `.env` 的 `ASSET_MCP_URL` 配置，例如：

```env
ASSET_MCP_URL=http://192.168.110.73:8010/mcp
```

当前工具：

- `list_tools`
- `get_run_context`
- `list_candidate_assets`
- `list_conversation_assets`
- `search_assets`
- `get_asset_summary`
- `read_asset_chunk`
- `get_asset_download_url`
- `get_artifact_upload_url`
- `complete_artifact`
- `report_progress(status)`

Codex 只能拿到 run-scoped 工具和 signed URL，不能拿对象存储长期凭据。

后续再补：

- 真正的 chunk 生成流水线；当前 `read_asset_chunk` 是按对象字节块读取，适合文本和已抽取文本，不适合直接理解二进制 Word/PDF。
- 更强的搜索排序；当前 `search_assets` 是 filename、summary、metadata、kind、role 的关键词匹配。

## MCP Token 后续安全事项

当前 `/mcp` 使用 run-scoped bearer token，目的是让同一个 MCP 地址可以服务多次 run，但每次 run 只能访问自己的候选资产和所属 conversation 范围内的资产。

已实现：

- 创建 run 时生成 token。
- `run_auth_tokens` 表保存 token hash。
- `/mcp` 校验 bearer token、状态和过期时间。
- 默认有效期 24 小时。

仍待实现：

- 不再把明文 token 长期写入 `codex_runs.metadata_json` / `command_json`。
- 增加 token rotate / revoke 接口。
- 增加过期 token 清理任务。
- 增加开发模式开关，例如 `ALLOW_INSECURE_MCP=true`，方便本机临时测试不带 token。
- 生产/多用户场景缩短默认 token TTL。

## 下一阶段：直接上传输出

目标：

1. Codex 为每个输出 artifact 请求上传 URL。
2. Codex 直接上传到 OSS。
3. Codex 调 complete API，提交 object key、size、sha256、filename、role、parent asset ids。
4. Gateway 校验对象存在和 checksum。
5. Gateway 登记 `assets`、`file_assets` 兼容行、`asset_lineage`、`run_assets` 和 assistant message attachments。
6. SSH runner 不再整目录下载远端 workspace。

## SSH runner 过渡事项

当前状态：

- `SshCodexRunner` 使用系统 `ssh` / `scp`，仍是 key-auth 的 fallback。
- `ParamikoPasswordCodexRunner` 在 `ssh_auth_method == "password"` 或保存了 `ssh_password` 时使用。
- 健康检查和远端清理也有同样分裂：密码设备用 Paramiko，密钥设备用 OpenSSH。

这些可以继续修，但优先级低于 OSS 数据面。只在当前 demo 必须依赖 SSH 稳定性时处理。

## 暂不删除 OpenSSH 的原因

这些路径还依赖它：

- Run 执行：`apps/worker/src/codex_gateway_worker/codex_runner.py`
  - `CodexRunner.run()` 会把 SSH run 派发给 `SshCodexRunner.run()`。
  - `SshCodexRunner.run()` 只有密码模式才交给 `ParamikoPasswordCodexRunner`。
  - 没保存密码的 key-auth 设备仍使用系统 `ssh` / `scp`。
- 健康检查：`apps/api/src/gateway/services/remote_check_service.py`
  - 密码设备用 `_check_with_paramiko()`。
  - 密钥设备用 `_check_with_openssh()`。
- 远端清理：`apps/api/src/gateway/services/remote_workspace_service.py`
  - 密码设备用 `_run_with_paramiko()`。
  - 密钥设备用 `_run_with_openssh()`。
- 测试：`apps/api/tests/test_milestones_1_3.py`
  - 仍导入 `SshCodexRunner` 并检查远端命令构造。
- 文档/UI 语义：
  - 设备仍暴露 `ssh_auth_method`、`ssh_identity_file`、`ssh_password`、`has_ssh_password`。

## 如果以后要统一 Paramiko

迁移步骤：

1. 增加 Paramiko key-auth 支持，使用 `ssh_identity_file`，必要时支持 passphrase。
2. 把 run 执行统一到一个 Paramiko runner。
3. 抽共享 SSH 连接、命令执行和清理 helper。
4. 健康检查从 `_check_with_openssh()` 迁到共享 Paramiko 实现。
5. 远端清理从 `_run_with_openssh()` 迁到共享 Paramiko 实现。
6. 更新测试，覆盖 auth 选择、命令执行、timeout、stdout/stderr 解析和清理。
7. 更新 Devices UI 和文档。
8. 删除系统 `ssh` / `scp` subprocess 路径。

统一前需要补齐：

- host key policy：known-hosts 路径、首次信任、host key 改变时报清楚错误。
- timeout：connect、banner/auth、命令运行、SFTP 上传、SFTP 下载分别设置。
- keepalive：长时间 Codex run 期间保持连接。
- channel 生命周期：timeout、stdin 写入失败、远端启动失败时可靠关闭。
- exit status：区分 Codex 失败、SSH 失败、SFTP 失败。
- stdout/stderr：保持 JSON stdout 解析稳定，同时保留 stderr 分类和尾行。
- shell 初始化：显式处理 `codex_executable` 所在目录，不依赖交互 shell profile。
- observability：连接、上传、远端命令启动、下载、清理都发结构化事件。

# 数据面架构

本文定义 Codex Workspace Gateway 后续扩展时的文件传输架构。目标是避免主服务器在文件量和 Codex 机器数量增长后变成大文件搬运瓶颈。

## 目标

Gateway 应该是控制面，而不是大文件传输路径。

- 主服务器：API、调度、权限、run registry、FilePlanner、审计。
- 对象存储：持久数据面，保存上传文件、派生文件和输出文件。
- Codex 设备：执行面。按需请求文件，直接从对象存储下载输入并上传结果。

主服务器仍可以创建 prompt、run manifest、设备配置等小控制文件。但一旦 OSS-backed execution path 建成，主服务器不应该再把完整 workspace 复制到远端设备，也不应该再从远端设备复制完整 workspace 回来。

## 当前瓶颈

当前远程执行链路仍会让 gateway 搬运两次大文件：

1. 上传文件先登记为 durable assets。
2. `SessionWorkspaceService` 准备本地 session workspace。
3. `DistributionService.materialize_plan()` 把选中的资产下载到 session workspace。
4. `SshCodexRunner` / `ParamikoPasswordCodexRunner` 通过 SCP/SFTP 把整个 workspace 复制到远端 Codex 设备。
5. 执行结束后，runner 把远端 workspace 复制回 gateway。
6. Gateway 扫描 `outputs/`，把生成文件登记为 durable assets。

这对 MVP 验证可以接受，但会让主服务器同时承担：

- 文件读写 IO。
- SSH/SFTP 加密开销。
- 上行带宽。
- 下行带宽。
- 调度。
- 日志流式转发。
- API 请求。

冲突链路集中在这些文件：

- `apps/api/src/gateway/services/session_workspace_service.py`
  - 创建完整本地 run workspace。
  - 执行前调用 `DistributionService.materialize_plan()`。
- `apps/api/src/gateway/services/distribution_service.py`
  - 把被选中的资产当作必须复制进 workspace 的文件。
- `apps/api/src/gateway/services/prompt_compiler.py`
  - 告诉 Codex 文件已经下载到 session workspace。
- `apps/worker/src/codex_gateway_worker/codex_runner.py`
  - SSH 设备执行前上传整个 workspace。
  - 执行后下载整个远端 workspace。
- `apps/api/src/gateway/services/file_service.py`
  - 通过扫描本地 session `outputs/` 登记输出。

这些不应该作为长期数据面继续强化。SSH 后续只应该负责命令执行、健康检查、清理和过渡期的小控制文件投递。

## 目标链路

```text
用户上传文件
  -> Gateway 接收上传并写入 OSS
  -> Gateway 记录 asset metadata、summary、derivative、owner、link

用户启动 run
  -> Gateway 选择设备
  -> Gateway 创建 run 和候选资产 manifest
  -> Gateway 把小型任务 manifest / prompt 发给 Codex

Codex 执行
  -> Codex 看到候选文件摘要和 derivative metadata
  -> Codex 通过 gateway 工具请求具体 asset / chunk
  -> Gateway 校验 run 权限并返回短期 signed URL
  -> Codex 需要时直接从 OSS 下载
  -> Codex 用短期 signed URL 直接上传输出到 OSS

Gateway 收尾
  -> Gateway 登记输出 artifact、lineage、output role、event
  -> Gateway 保存 final message 和 run status
```

## 文件访问模型

Codex 不应该面对整个对象存储自己挑文件。访问要分三层。

第一层：`FilePlanner` 根据 metadata 生成小候选集：

- 当前 user、project、conversation 和本次 run attachments。
- `material` vs `guidance`。
- 最新版本 / `branch_key`。
- 文件 role 和 MIME type。
- summary、derivative、chunk、preview。
- 用户本次任务意图和最近消息上下文。

第二层：Codex 在执行时按需决定读什么：

- `search_assets(query)`
- `get_asset_summary(asset_id)`
- `read_asset_chunk(asset_id, chunk_id)`
- `download_asset(asset_id)`
- `upload_artifact(path, role)`
- `report_progress(status)`

第三层：Gateway 对每次请求继续授权：

- asset 属于该 run 的 allowed candidate set，或属于显式允许扩展的范围。
- asset 属于正确的 user/project/conversation。
- signed URL 过期时间足够短。
- 请求操作符合 read/write 权限。
- 记录审计日志。
- 可选限制：size、次数、MIME type、下载次数。

## Manifest 形态

Run manifest 应该变成权限和发现 manifest，而不是“已经复制好的文件列表”。

示例：

```json
{
  "run_id": "run_123",
  "session_id": "sess_123",
  "task": "修改最新 Word 文档并导出 PDF",
  "data_plane": {
    "mode": "oss_signed_url",
    "tool_server": "gateway-assets",
    "signed_url_ttl_seconds": 900
  },
  "candidate_assets": [
    {
      "asset_id": "asset_doc_latest",
      "filename": "实习报告_v3.docx",
      "kind": "material",
      "role": "source_doc",
      "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "size_bytes": 183024,
      "sha256": "...",
      "branch_key": "实习报告.docx",
      "version_no": 3,
      "summary": "用户最近上传的实习报告正文，包含项目背景、技术方案、总结。",
      "why_included": "最新 Word 文档，和用户任务直接相关",
      "available_derivatives": [
        {
          "derivative_id": "deriv_text",
          "type": "extracted_text",
          "summary": "正文纯文本，可先读它判断是否需要原始 docx。"
        }
      ]
    }
  ],
  "output_policy": {
    "allowed_roles": ["final", "revision", "diagnostic"],
    "default_prefix": "runs/run_123/outputs/"
  }
}
```

## API 和工具面

Gateway 应该暴露两类能力。

给 worker helper 或非模型客户端使用的 HTTP endpoint：

- `POST /api/runs/{run_id}/assets/{asset_id}/download-url`
- `POST /api/runs/{run_id}/artifacts/upload-url`
- `POST /api/runs/{run_id}/artifacts/complete`
- `GET /api/runs/{run_id}/asset-candidates`
- `GET /api/runs/{run_id}/assets/{asset_id}/chunks/{chunk_id}`

给 Codex 执行时使用的 MCP tools：

- `search_assets`
- `get_asset_summary`
- `read_asset_chunk`
- `download_asset`
- `upload_artifact`
- `report_progress`

MCP server 可以直接由 gateway process 提供，也可以先做一个小 sidecar 调 gateway API。关键边界是：Codex 拿到的是 scoped tools 和 signed URL，不是对象存储长期凭据。

## Run Token 设计

`/mcp` 暴露在主服务器 HTTP 端口上后，同一局域网内的其他机器也可能访问到它。MCP 工具又能列出资产、生成对象存储临时下载 URL、生成上传 URL、登记输出 artifact，所以不能只靠“知道地址的人才会调用”作为边界。

当前设计使用 run-scoped bearer token：

```text
ASSET_MCP_TOKEN -> run_auth_tokens.token_hash -> run_id -> conversation_id/user_id -> allowed assets
```

不要用 `codex mcp add` 做每次任务配置，因为它会写入远端 Codex 的持久配置。每次自动化 run 应通过 `codex exec -c ...` 临时注入 MCP 配置，并配合 `--ephemeral` 和 `--ignore-user-config` 隔离全局配置。

主服务器的局域网 MCP 地址由项目根目录 `.env` 的 `ASSET_MCP_URL` 控制。例如 `ASSET_MCP_URL=http://192.168.110.73:8010/mcp`；后端创建 run 时会把这个 URL 写入本次 `codex exec -c` 参数。

```bash
ASSET_MCP_TOKEN="本次run的token" codex exec \
  --ephemeral \
  --ignore-user-config \
  -c 'mcp.remote_mcp_client_enabled=true' \
  -c 'mcp_servers.asset.url="http://主服务器IP:8010/mcp"' \
  -c 'mcp_servers.asset.bearer_token_env_var="ASSET_MCP_TOKEN"' \
  -c 'mcp_servers.asset.required=true' \
  -c 'mcp_servers.asset.enabled_tools=["list_candidate_assets","search_assets","get_asset_download_url"]' \
  ...
```

这样做的目的：

- 防止同一局域网其他机器直接调用 `/mcp` 拿文件下载 URL。
- 防止一个 run 读取另一个 run / conversation 的文件。
- 让 MCP 配置只作用于单次 run，不污染 `~/.codex/config.toml`。
- 让旧 shell、旧任务、旧 token 在过期后失效。

当前已实现：

- 创建 run 时生成 token。
- 数据库只保存 token hash。
- `/mcp` 每次请求校验 `Authorization: Bearer ...`。
- 校验 token 是否存在、状态是否 active、是否过期。
- token 默认有效期为 24 小时。
- token 校验成功后只按对应 `run_id` 做资产访问控制。

当前仍需补齐：

- 不应长期把明文 token 写入 `codex_runs.metadata_json` / `command_json`；后续应改为只在创建 run 的响应或一次性读取接口里返回。
- 增加 token rotate / revoke 接口。
- 增加过期 token 的定期清理。
- 增加开发模式开关，例如 `ALLOW_INSECURE_MCP=true`，只在本机调试时允许不带 token。
- 根据部署模式缩短默认有效期，例如从 24 小时降到 15-60 分钟。

## 输出登记

长期输出路径要避免把远端 workspace 复制回来。

1. Codex 为每个结果 artifact 请求 upload URL。
2. Codex 直接上传到 OSS。
3. Codex 调用 artifact completion，提交 object key、size、sha256、filename、role 和可选 parent asset ids。
4. Gateway 校验对象并登记：
   - `assets`
   - `file_assets` 兼容行，如果当前 UI 仍需要
   - `asset_lineage`
   - `run_assets`
   - final assistant message attachments

本地 session output scanning 只保留给 local/dev run，直到 direct upload path 完成。

## 要删除或降级的内容

长期远程执行路径里要删除：

- 远程 run 前完整 materialize workspace。
- 通过 SCP/SFTP 上传 `materials/`、`guidance/`、`versions/` 和旧 outputs。
- 通过 SCP/SFTP 下载完整远端 workspace。
- prompt 里“选中文件已下载到当前 workspace”的说法。
- 把 SFTP retry/progress 当作产品级扩展策略继续投入。

保留或改造：

- `FilePlanner`：保留，但输出从 `materialize/reference_only` 改成 `candidate/required_guidance/deferred/ignored`。
- `DistributionService`：重命名或拆成 run asset authorization planner。
- `SessionWorkspaceService`：local run 继续用于 scratch/control files；remote run 只创建小控制目录和 session 记录。
- `StorageService`：保留为 OSS 抽象；增加 presigned upload 和按操作区分的过期时间。
- `run_assets`：保留，用于记录候选、下载、chunk 读取、上传 artifact 的审计和 lineage。
- SSH device records 和 health check：保留为执行面连接能力，不再承担数据面传输。

## 迁移计划

### 阶段 1：文档化并隔离当前传输路径

- 增加本文档。
- 标记当前 SFTP workspace sync 是过渡方案。
- 除非 demo 立即需要，不再继续优化大文件 SFTP。

### 阶段 2：增加 signed URL 基础能力

- 已增加 `StorageService.presign_upload()`。
- 已增加 run-scoped download/upload URL endpoints。
- 增加 URL 发放和 artifact completion 的审计记录。
- 只有确实需要时才加 DB 字段；如果本地 SQLite 旧数据冲突，直接清理 `data/` 后重跑。

### 阶段 3：改变 run manifest

- 已开始用 candidate assets 扩展 materialized files。
- 已包含 summary、reason、branch、version、role、kind 等候选信息。
- 已更新 prompt，让 Codex 看到候选资产，并知道可以通过 MCP 工具按需获取更多信息。
- 后续继续补 derivative、chunk 和 allowed operations。

### 阶段 4：提供 Codex 资产工具

- 已增加基础 HTTP MCP endpoint：`/mcp`。
- 当前实际工具名为：`list_tools`、`get_run_context`、`list_candidate_assets`、`list_conversation_assets`、`get_asset_summary`、`get_asset_download_url`、`get_artifact_upload_url`、`report_progress`。
- 已补 `search_assets`、`read_asset_chunk`、artifact complete 和 direct output registration 的第一版。
- `read_asset_chunk` 当前是按对象字节块读取；真正面向 Word/PDF/Excel 的 chunk 仍需要后续派生物流水线。
- 只给 gateway run 配置这些 MCP server。

### 阶段 5：直接上传输出

- 让 Codex 直接上传生成 artifacts 到 OSS。
- 已通过 artifact completion API 登记输出。
- 停止下载完整远端 workspace。
- 只保留下载 `final.md`，或者让 Codex 通过 report/artifact API 回传最终消息。

### 阶段 6：移除远程 workspace sync

- 已从正常 SSH runner 中移除完整 session workspace 的 SCP/SFTP 上传和下载。
- 当前仍保留远端目录创建和 `final.md` 单文件取回。
- 远端 cleanup 只清理 scratch/control 目录。
- 删除假设 full workspace sync 的旧 prompt 和文档。
- 更新测试，覆盖 run manifest authorization、signed URL 发放和输出登记。

## 近期规则

下一步不要试图把 SFTP 做成可扩展数据面。先做 OSS signed URL 和资产工具路径，再让旧 SFTP workspace sync 自然退出。

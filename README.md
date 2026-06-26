# Codex Workspace Gateway

这是一个开发/MVP 形态的 Codex 工作区网关：FastAPI 接收对话、上传文件和运行请求，把文件登记为对象存储里的 durable assets，再为每次 run 创建临时 session workspace，最后调用本地或 SSH 设备上的 `codex exec`。

当前重点是让文件、运行、设备和事件链路清楚、可调试、可本地复现。

## 已有能力

- 创建默认用户的 conversation。
- 上传文件到 S3 兼容对象存储，本地开发使用 MinIO。
- 将上传文件登记为 `assets` / `file_assets`。
- 上传文件分为 `material` 和 `guidance`。
- 单独上传会写入一条带附件的 user message。
- 同一 conversation、同一 kind、同一原始文件名再次上传不同内容时，新文件替换旧文件。
- 为每次 run 创建临时 `session workspace`。
- 根据本次消息附件和后端管理的 guidance 文件生成分发计划。
- 执行 `codex exec` 并保存 `final.md`、`codex.jsonl` 和事件。
- 管理本地/SSH Codex 设备，设备路径存 SQLite，不从 `.env` seed。
- 将 `outputs/` 中的生成文件登记回 durable assets。

## 启动

启动 API：

```powershell
make api
```

API 地址：

```text
http://127.0.0.1:8010
```

启动测试 UI：

```powershell
make web
```

Web 地址：

```text
http://127.0.0.1:5173
```

也可以直接在浏览器打开：

```text
apps/web/index.html
```

健康检查：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8010/api/health
```

## 常用接口

- `POST /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `POST /api/conversations/{conversation_id}/messages`
- `POST /api/conversations/{conversation_id}/files`
- `GET /api/conversations/{conversation_id}/files`
- `GET /api/conversations/{conversation_id}/assets`
- `POST /api/conversations/{conversation_id}/runs`
- `GET /api/conversations/{conversation_id}/runs`
- `GET /api/conversations/{conversation_id}/events`
- `POST /api/conversations/run`
- `GET /api/assets/{asset_id}`
- `GET /api/runs/{run_id}/asset-candidates`
- `POST /api/runs/{run_id}/assets/search`
- `GET /api/runs/{run_id}/assets/{asset_id}/chunks/{chunk_index}`
- `POST /api/runs/{run_id}/assets/{asset_id}/download-url`
- `POST /api/runs/{run_id}/artifacts/upload-url`
- `POST /api/runs/{run_id}/artifacts/complete`
- `GET /api/files/{file_id}/download`
- `GET /api/devices`
- `POST /api/devices`
- `PATCH /api/devices/{device_id}`
- `DELETE /api/devices/{device_id}`
- `POST /api/devices/{device_id}/health-check`

## 配置

复制 `.env.example`，或者直接设置环境变量：

```text
GATEWAY_DATA_DIR=data
DEFAULT_USER_ID=user_default
DEFAULT_CONVERSATION_ID=1
CODEX_MAX_RUNTIME_SECONDS=900
OBJECT_STORAGE_ENDPOINT=http://127.0.0.1:19000
OBJECT_STORAGE_BUCKET=mathpilot-dev
OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin
OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin123
OBJECT_STORAGE_REGION=us-east-1
OBJECT_STORAGE_ADDRESSING_STYLE=path
OBJECT_STORAGE_SIGNATURE_VERSION=s3v4
OBJECT_STORAGE_AUTO_CREATE_BUCKET=true
```

## 对象存储

网关使用一条 S3 兼容对象存储路径，同时覆盖本地测试和生产 OSS。 本地开发先启动 MinIO：

```powershell
docker compose -f docker-compose.minio.yml up -d
```

MinIO 控制台：

```text
http://127.0.0.1:19001
```

默认账号：

```text
minioadmin / minioadmin123
```

Compose 会自动创建 `mathpilot-dev` bucket。生产 OSS 只需要替换 endpoint、bucket、凭据、addressing style 和 signature version。

对象存储凭据只在主服务器上使用，不传给 Codex 设备。当前远程 run 仍会收到 materialized session workspace；目标架构会改成 Codex 设备按需通过 signed URL 直连 OSS。

## 上传语义

上传文件分为两类：

- `material`：当前 conversation 的业务材料。当前 run 默认只把本次消息显式附件作为业务材料候选。
- `guidance`：SOP、写作规范、规则、skill 等后端管理文件。通常每次 run 都会挂载或纳入候选。

上传 metadata 仍保留 `description` 字段，但当前 UI 不要求用户填写。后续可以由摘要/索引流程补充，用于按需检索材料。

## 会话工作区

每次 run 会创建临时目录：

```text
GATEWAY_DATA_DIR/sessions/{session_id}
```

当前实现会把本次 run 需要的材料和 guidance 文件 materialize 到 session，写入运行上下文，然后从 session root 执行 Codex。过期 session 可以清理：

```powershell
python scripts/clean_expired_sessions.py
```

分发计划由策略层生成。当前默认仍 materialize 原文件，但 manifest 中已经包含 `mode`、`strategy`、`reason`、`derivative_id` 等字段，后续可以平滑接入文本抽取、chunk、archive manifest、preview 等派生物。

## Codex 设备

通过 `/api/devices` 或测试 UI 添加和管理 Codex 设备。设备路径存 SQLite，不写 `.env`。

SSH 设备必须显式填写远端 `codex_executable` 绝对路径，例如：

```text
/home/openclaw/.local/bin/codex
```

不要依赖远端非交互 shell 的 `PATH`。

密码 SSH 设备需要：

```powershell
pip install paramiko
```

运行任务前，可以在 Devices 面板检查 SSH 和远端 Codex，也可以调用：

```text
POST /api/devices/{device_id}/health-check
```

## Asset MCP

Gateway 暴露 run-scoped asset MCP endpoint：

```text
http://主服务器IP:8010/mcp
```

本服务器的局域网 IP 写在项目根目录 `.env` 里的 `ASSET_MCP_URL`。后端创建 run 时会把这个地址写进本次 `codex exec -c` 的临时 MCP 配置：

```text
ASSET_MCP_URL=http://主服务器IP:8010/mcp
```

例如你的主服务器 IP 是 `192.168.110.73`，就在 `.env` 改成：

```env
ASSET_MCP_URL=http://192.168.110.73:8010/mcp
```

每个 run 创建时会生成一个 `ASSET_MCP_TOKEN`。不要用 `codex mcp add` 做每次任务配置，因为它会写入远端 Codex 的持久配置。当前 runner 会在本次 `codex exec` 命令中用 `-c` 临时注入 MCP 配置，并配合 `--ephemeral` 和 `--ignore-user-config` 隔离全局配置。

等价命令形态：

```bash
ASSET_MCP_TOKEN="run_xxx_token" codex exec \
  --ephemeral \
  --ignore-user-config \
  --sandbox workspace-write \
  -C /path/to/workspace \
  -c 'mcp.remote_mcp_client_enabled=true' \
  -c 'mcp_servers.asset.url="http://主服务器IP:8010/mcp"' \
  -c 'mcp_servers.asset.bearer_token_env_var="ASSET_MCP_TOKEN"' \
  -c 'mcp_servers.asset.required=true' \
  -c 'mcp_servers.asset.enabled_tools=["list_candidate_assets","search_assets","get_asset_download_url"]' \
  -
```

当前工具包括：

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
- `report_progress`

当前 SSH runner 不再通过 SCP/SFTP 同步完整 session workspace，只创建远端空 workspace 和必要目录，并取回最终消息文件。文件数据面后续通过 MCP + signed URL 访问。

## 测试和排查

运行测试：

```powershell
make test
```

运行全部测试入口；有 Vitest 配置时会跑前端 Vitest，没有则跳过；后端优先跑 pytest，未安装 pytest 时回退到 unittest：

```powershell
make test-all
```

查看 SQLite：

```powershell
make db
```

完整代码链路说明：

```text
docs/full-flow-guide.md
```

沙箱和 workspace 边界说明：

```text
docs/security.md
```

控制面/数据面/执行面目标架构，以及迁移掉远程 SFTP workspace sync 的规划：

```text
docs/architecture-data-plane.md
```

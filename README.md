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

## 测试和排查

运行测试：

```powershell
make test
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

# AGENTS.md

本仓库是 Codex Workspace Gateway：用 FastAPI 接收对话、文件和运行请求，把上传文件登记为持久资产，按需挂载到一次性 session workspace，再调用本地或 SSH 设备上的 `codex exec`。

## 工作偏好

- 如果数据库结构或本地数据会冲突，直接说明需要删库或清理 `data/` 后重跑，不要堆隐藏兼容逻辑。
- 任务完成后不要做重复验证；只有当必须通过命令才能确认修改是否正确时，才运行必要验证。
- 修改前先读相关文件链路，避免只看前端或只看单个服务就下结论。
- 这个仓库目前是开发/MVP 形态，优先让代码路径清楚、可调试、可本地复现。
- Codex device 是数据库状态，不从 `.env` 预读或 seed；创建 SSH device 时必须显式填写远端 `codex_executable` 绝对路径。

## 目录结构

- `apps/api/src/gateway/`：FastAPI 后端和核心业务逻辑。
  - `main.py` 注册应用、中间件和 routers。
  - `routers/` 放 HTTP API。对话、上传、运行、设备、资产等入口在这里。
  - `services/` 放业务服务。常见链路包括 conversation、file、asset、run、session、distribution、prompt、device。
  - `db.py` 定义 SQLite schema 和轻量迁移。
  - `config.py` 读取环境变量。
- `apps/api/tests/`：后端 unittest，主要覆盖 milestone 流程、上传、运行、资产和 session。
- `apps/worker/src/codex_gateway_worker/`：实际执行 `codex exec` 的 runner，包含本地和 SSH 运行逻辑。
- `apps/web/index.html`：无框架测试 UI。前端行为都在这个单文件内。
- `scripts/`：本地辅助脚本，例如启动 API、检查 SSH Codex、清理 session/workspace、查看 SQLite。
- `docs/`：API、开发、完整流程、安全和 workspace 文档。
- `data/`：本地运行数据。通常包含 SQLite、对象存储映射、session workspace 和运行输出，不应把它当源码改动。
- `.agents/`、`.codex/`：本地 agent/Codex 配置目录，除非任务明确要求，否则不要改。

## 核心数据流

1. 前端 `apps/web/index.html` 调用后端 API。
2. `POST /api/conversations` 创建 conversation，可同时写入一条 user message。
3. `POST /api/conversations/{conversation_id}/files` 保存文件资产，并为这次上传新增一条 user message；该 message 的 `attachments_json` 包含本次上传文件 id。
4. `POST /api/conversations/{conversation_id}/messages` 会创建带 `attachment_ids` 的 user message。
5. `POST /api/conversations/{conversation_id}/runs` 或 `POST /api/conversations/run` 会创建 run；其中 `/run` 是 multipart 一次性提交路径，会上传文件、把文件 id 作为附件写入新 message，再启动 run。
6. `RunService.queue_run()` 创建 `codex_runs`、选择设备、创建 session，并记录本次 run 的 `attachment_ids`。
7. `SessionWorkspaceService` 和 `DistributionService` 决定哪些持久资产挂载到 session workspace。
8. `PromptCompiler` 写入 `prompt.md`、`task.json`、`.gateway/manifest.json` 等运行上下文。
9. `CodexRunner` 在 session root 执行 `codex exec`，完成后后端登记输出资产和事件。

## 上传与附件语义

- `file_assets`/`assets` 表示资产库中的文件。
- `messages.attachments_json` 表示某条用户消息显式附带的文件 id。
- 单独点击“上传保存”属于 conversation 级资产上传，同时也要作为独立 user message 进入对话历史。
- `messages.attachments_json` 只表示某条用户消息显式附带的文件 id；上传消息应只包含本次上传文件，不要回写到旧消息。
- 同一 conversation、同一 kind、同一原始文件名再次上传时，如果 SHA-256 不同，新文件直接替换旧文件；旧文件不再出现在 `file_assets` 列表或后续 run 候选中。
- `guidance` 文件是后端管理的规则、SOP、技能文件，通常每次 run 都会挂载。
- `material` 文件目前会临时作为当前 conversation 的后续 run 附件候选；TODO: 后续改为按用户意图筛选有用文件再 materialize 到 session。

## 常用命令

```powershell
make api
make web
make test
make db
make ssh-check
```

API 默认地址：

```text
http://127.0.0.1:8010
```

测试 UI 默认地址：

```text
http://127.0.0.1:5173
```

MinIO 本地对象存储：

```powershell
docker compose -f docker-compose.minio.yml up -d
```

## 开发注意事项

- Python 版本要求 `>=3.12`，依赖见 `pyproject.toml`。
- FastAPI app 入口是 `gateway.main:app`，运行时要带 `--app-dir apps/api/src`。
- 测试使用 `python -m unittest discover -s apps/api/tests -p "test_*.py"`。
- 本地数据库和对象文件都在 `GATEWAY_DATA_DIR` 下，默认是 `data/`。
- 运行相关状态以 SQLite 和事件表为准；UI 只是测试入口。
- 修改上传、附件、资产、session 分发时，优先检查这些文件：
  - `apps/api/src/gateway/routers/conversations.py`
  - `apps/api/src/gateway/services/conversation_service.py`
  - `apps/api/src/gateway/services/file_service.py`
  - `apps/api/src/gateway/services/asset_service.py`
  - `apps/api/src/gateway/services/distribution_service.py`
  - `apps/api/src/gateway/services/session_workspace_service.py`
  - `apps/api/src/gateway/services/run_service.py`
  - `apps/web/index.html`
- 修改设备创建、选择、健康检查时，优先检查：
  - `apps/api/src/gateway/services/device_service.py`
  - `apps/api/src/gateway/services/remote_check_service.py`
  - `apps/api/src/gateway/routers/devices.py`
  - `apps/api/src/gateway/services/run_service.py`
  - `apps/web/index.html`

## 不要做

- 不要把对象存储凭据传给 Codex 运行设备；设备只拿已挂载的 session workspace。
- 不要让“上传保存”和“上传并运行”落到不同 conversation；前端提交 run 时要传当前 `conversation_id`。
- 不要把 Codex 设备路径写回 `.env`；路径应在创建设备时进入 SQLite。
- 不要改动 `data/` 里的运行数据来“修复”源码问题；必要时让用户清空后重跑。
- 不要提交缓存、`__pycache__`、临时 session、MinIO 数据或本地 IDE 文件。

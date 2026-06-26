# 全流程测试和代码追踪指南

这份指南按一次真实任务来走：打开前端、上传文件、创建 conversation、启动 run，然后顺着代码、数据库和磁盘目录理解当前项目。

## 1. 启动项目

启动 API：

```powershell
python -m uvicorn gateway.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8010
```

API 地址：

```text
http://127.0.0.1:8010
```

启动简单前端：

```powershell
python -m http.server 5173 -d apps/web
```

前端地址：

```text
http://127.0.0.1:5173
```

也可以直接打开：

```text
apps/web/index.html
```

## 2. 前端入口

前端文件：

```text
apps/web/index.html
```

它是无框架测试 UI，只用浏览器 `fetch` 调后端 API。

常用动作对应关系：

| 页面动作 | HTTP API | 后端入口 |
|---|---|---|
| 检查 API | `GET /api/health` | `apps/api/src/gateway/routers/health.py` |
| 上传保存 | `POST /api/conversations/{id}/files` | `apps/api/src/gateway/routers/conversations.py` |
| 追加消息 | `POST /api/conversations/{id}/messages` | `apps/api/src/gateway/routers/conversations.py` |
| 启动 run | `POST /api/conversations/{id}/runs` | `apps/api/src/gateway/routers/conversations.py` |
| 一键上传并运行 | `POST /api/conversations/run` | `apps/api/src/gateway/routers/conversations.py` |
| 刷新状态 | `GET conversation/files/assets/runs/events` | `apps/api/src/gateway/routers/conversations.py` |
| 管理设备 | `GET/POST/PATCH/DELETE /api/devices` | `apps/api/src/gateway/routers/devices.py` |

## 3. 上传并运行发生什么

前端一键提交：

```http
POST /api/conversations/run
Content-Type: multipart/form-data
```

典型字段：

```text
title = 整理原始数据
task_type = file_task
user_instruction = 请处理我上传的文件
kind = material
files = raw_data.xlsx
```

后端入口：

```text
apps/api/src/gateway/routers/conversations.py
submit_and_run()
```

它会：

1. 创建或复用默认用户当前 conversation。
2. 上传文件并登记资产。
3. 创建一条 user message，把本次上传 file ids 写入 `attachments_json`。
4. 创建 run。
5. 把实际执行放入 FastAPI background task。

默认用户和默认 conversation 来自：

```text
apps/api/src/gateway/config.py
```

常见默认值：

```text
DEFAULT_USER_ID=user_default
DEFAULT_CONVERSATION_ID=1
GATEWAY_DATA_DIR=data
```

SQLite 默认位置：

```text
data/gateway.sqlite3
```

## 4. 上传文件链路

上传入口：

```text
apps/api/src/gateway/routers/conversations.py
upload_file()
```

核心服务：

```text
apps/api/src/gateway/services/file_service.py
FileService.save_upload()
```

资产服务：

```text
apps/api/src/gateway/services/asset_service.py
AssetService.create_from_path()
```

对象存储服务：

```text
apps/api/src/gateway/services/storage_service.py
StorageService.put_path()
```

上传规则：

- `kind=material` 表示业务材料。
- `kind=guidance` 表示 SOP、写作规范、规则、skill 等后端管理文件。
- 保存前计算 `sha256`。
- 同一 conversation、同一 kind、同一原始文件名再次上传时，如果内容不同，新文件替换旧文件。
- `description` 存入 metadata，但当前 UI 不要求填写。
- 上传成功后写入 `file_assets` 和 `assets`。
- 单独上传会创建一条 user message，附件只包含本次上传的文件 id。

重要表：

```text
file_assets
assets
asset_links
asset_branches
asset_lineage
```

路径安全检查：

```text
apps/api/src/gateway/services/path_security.py
```

这里会拒绝 `../`、绝对路径和任何逃出 workspace 的路径。

## 5. 运行创建链路

启动 run 的入口：

```text
apps/api/src/gateway/routers/conversations.py
start_run()
```

核心服务：

```text
apps/api/src/gateway/services/run_service.py
RunService.queue_run()
```

`queue_run()` 会：

1. 创建 `run_id`。
2. 选择 Codex device。
3. 创建 `run_sessions` 记录和 session workspace。
4. 在 `codex_runs` 表写入 run 记录。
5. 把本次 user message 的 `attachment_ids` 写入 run metadata。
6. 记录 `run_created` 事件。

设备选择和设备状态：

```text
apps/api/src/gateway/services/device_service.py
```

## 6. 会话工作区

Session 服务：

```text
apps/api/src/gateway/services/session_workspace_service.py
```

每次 run 创建：

```text
data/sessions/{session_id}/
  materials/
  guidance/
  outputs/
  logs/
  versions/
  .gateway/
```

Session 记录在：

```text
run_sessions
```

过期 session 清理：

```powershell
python scripts/clean_expired_sessions.py
```

## 7. 文件分发计划

分发计划入口：

```text
apps/api/src/gateway/services/distribution_service.py
DistributionService.build_plan()
```

当前策略实现：

```text
apps/api/src/gateway/services/file_planner.py
FilePlanner.plan()
```

当前规则大致是：

- guidance 文件通常参与 run。
- material 文件优先来自本次消息显式附件。
- 根据任务关键词粗略选择 docx、spreadsheet 等。
- 没有强匹配时用少量最新 material 作为 fallback。
- 大文件后续应通过 derivative/chunk/summary 优化。

执行前 materialize：

```text
DistributionService.materialize_plan()
```

它会把选中的 durable assets 从对象存储下载到 session workspace，并写入 `run_assets`。

注意：这是当前 MVP 过渡路径。长期目标不是提前搬文件，而是给 Codex 候选资产 manifest，让 Codex 按需请求 signed URL 直连 OSS。详见：

```text
docs/architecture-data-plane.md
```

## 8. Prompt 编译

Prompt 编译服务：

```text
apps/api/src/gateway/services/prompt_compiler.py
PromptCompiler.compile()
```

它会根据分发计划生成 `prompt.md`，并写入 conversation workspace，再由 run 服务复制到 session workspace。

当前 prompt 会告诉 Codex：

- 本次用户请求是什么。
- 哪些业务文件已挂载。
- 哪些 guidance 文件需要优先读取。
- 输出应写入 `outputs/`。
- 不要读取 workspace 外路径。
- 不要访问网络。

## 9. Codex CLI 调用

后台执行入口：

```text
apps/api/src/gateway/services/run_service.py
RunService.execute_run()
```

Runner：

```text
apps/worker/src/codex_gateway_worker/codex_runner.py
CodexRunner.run()
```

本地命令形状：

```powershell
codex exec -C <session_workspace> `
  --sandbox workspace-write `
  --skip-git-repo-check `
  --json `
  --output-last-message <final_message_path> `
  -
```

Prompt 文本通过 stdin 传入。

关键参数含义：

- `-C <session_workspace>`：让 Codex 以 session workspace 为工作根目录。
- `--sandbox workspace-write`：限制写入范围。
- `--skip-git-repo-check`：session workspace 不需要是 Git 仓库。
- `--json`：让 Codex 以 JSONL 输出事件。
- `--output-last-message`：把最终 assistant message 保存到指定文件。

## 10. SSH 执行链路

SSH runner 仍是过渡路径：

```text
apps/worker/src/codex_gateway_worker/codex_runner.py
SshCodexRunner
ParamikoPasswordCodexRunner
```

当前流程：

1. 本地准备 session workspace。
2. SCP/SFTP 上传整个 session workspace 到远端。
3. 远端执行 `codex exec`。
4. SCP/SFTP 下载整个远端 workspace 回本地。
5. 本地扫描 `outputs/` 登记输出。

这条路径会造成主服务器搬大文件，后续要迁移到 OSS signed URL 数据面。

## 11. 输出登记

成功后执行：

```text
apps/api/src/gateway/services/file_service.py
FileService.register_run_outputs()
```

它会扫描：

```text
data/sessions/{session_id}/outputs/
```

并登记生成文件：

- 写入对象存储。
- 写入 `assets`。
- 写入 `file_assets` 兼容记录。
- 写入 `asset_lineage`。
- 写入 `run_assets`。
- 最终 assistant message 会附带输出说明。

## 12. 事件和日志

事件服务：

```text
apps/api/src/gateway/services/event_service.py
```

常见事件：

| 事件 | 产生位置 |
|---|---|
| `conversation_created` | `ConversationService.create()` |
| `file_uploaded` | `FileService.save_upload()` |
| `run_created` | `RunService.queue_run()` |
| `prompt_compiled` | `PromptCompiler.compile()` |
| `codex_started` | `RunService.execute_run()` |
| `codex_json_event` | `CodexRunner` stdout 回调 |
| `codex_stdout` | `CodexRunner` stdout 回调 |
| `codex_stderr` | `CodexRunner` stderr 回调 |
| `codex_completed` | `RunService.execute_run()` |
| `codex_failed` | `RunService.execute_run()` |
| `conversation_completed` | `RunService.execute_run()` |
| `conversation_failed` | `RunService.execute_run()` |

事件表：

```text
events
```

运行日志目录：

```text
data/conversations/{conversation_id}/runs/{run_id}/
  final.md
  codex.jsonl
```

前端通过这个接口轮询：

```http
GET /api/conversations/{conversation_id}/events
```

UI 展示平台事件、Codex JSONL 事件、stdout/stderr 摘要和最终消息，不展示模型隐藏思维链。

## 13. 手动检查

看数据库：

```powershell
python scripts/inspect_db.py
```

只看某张表：

```powershell
python scripts/inspect_db.py --table conversations
python scripts/inspect_db.py --table messages
python scripts/inspect_db.py --table file_assets
python scripts/inspect_db.py --table assets
python scripts/inspect_db.py --table codex_runs
python scripts/inspect_db.py --table run_sessions
python scripts/inspect_db.py --table run_assets
python scripts/inspect_db.py --table events
```

如果安装了 `sqlite3`：

```powershell
sqlite3 data/gateway.sqlite3
```

进入后：

```sql
.tables
.headers on
.mode column
select * from conversations;
select * from messages;
select * from file_assets;
select * from assets;
select * from codex_runs;
select * from run_sessions;
select * from events;
```

看 session 文件：

```text
data/sessions/{session_id}/
```

看 run 日志：

```text
data/conversations/{conversation_id}/runs/{run_id}/codex.jsonl
data/conversations/{conversation_id}/runs/{run_id}/final.md
```

## 14. 测试

测试目录：

```text
apps/api/tests/
```

运行：

```powershell
python -m unittest discover -s apps/api/tests -p "test_*.py"
```

常见覆盖点：

- conversation 创建。
- 上传和资产登记。
- session workspace。
- run 创建。
- Codex 命令参数。
- 路径越界拒绝。
- 输出登记。

## 15. 下一步

近期架构重点：

- `StorageService.presign_upload()`。
- run-scoped download/upload URL。
- artifact complete API。
- 候选资产 manifest。
- Codex 资产工具或 MCP server。
- 远程 run 移除完整 workspace SFTP 同步。

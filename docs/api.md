# API

基础地址：

```text
http://127.0.0.1:8010
```

## 健康检查

`GET /api/health`

返回 API 是否可用。

## 对话

创建 conversation：

`POST /api/conversations`

```json
{
  "title": "整理原始数据为规范 Excel 附件",
  "task_type": "excel_attachment",
  "user_request": "请整理成专著附件格式"
}
```

读取 conversation：

`GET /api/conversations/{conversation_id}`

追加用户消息：

`POST /api/conversations/{conversation_id}/messages`

```json
{
  "content": "请基于我刚上传的文件继续处理",
  "attachment_ids": ["file_xxx"]
}
```

读取默认用户当前 conversation：

`GET /api/conversations/current`

返回默认用户最新的未归档 conversation。

## 文件上传和下载

上传文件：

`POST /api/conversations/{conversation_id}/files`

multipart 字段：

- `file`：上传文件。
- `kind`：`material` 或 `guidance`。
- `description`：可选 metadata；当前 UI 不强制填写。

列出 conversation 文件：

`GET /api/conversations/{conversation_id}/files`

按原始文件名和 sha256 分支查看文件：

`GET /api/conversations/{conversation_id}/file-branches`

下载文件：

`GET /api/files/{file_id}/download`

## 资产

列出 conversation 资产：

`GET /api/conversations/{conversation_id}/assets`

读取单个资产 metadata：

`GET /api/assets/{asset_id}`

`assets` 是对象存储中的 durable asset，`file_assets` 是兼容当前 UI 和旧文件列表的文件记录。

列出 run 候选资产：

`GET /api/runs/{run_id}/asset-candidates`

搜索当前 run 所属 conversation 的 ready assets：

`POST /api/runs/{run_id}/assets/search`

```json
{
  "query": "实习报告 模板",
  "limit": 20
}
```

按字节块读取 asset 内容：

`GET /api/runs/{run_id}/assets/{asset_id}/chunks/{chunk_index}?chunk_size=8192`

为 run 内允许访问的资产生成短期下载 URL：

`POST /api/runs/{run_id}/assets/{asset_id}/download-url`

为 run 输出 artifact 生成短期上传 URL：

`POST /api/runs/{run_id}/artifacts/upload-url`

```json
{
  "filename": "result.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "role": "revision"
}
```

Codex 直传 artifact 到对象存储后，登记为正式输出资产：

`POST /api/runs/{run_id}/artifacts/complete`

```json
{
  "object_key": "users/user_default/conversations/1/runs/run_xxx/pending-artifacts/result.docx",
  "filename": "result.docx",
  "size_bytes": 12345,
  "sha256": "可选；传了就校验",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "role": "revision",
  "parent_asset_ids": ["file_xxx"]
}
```

## Asset MCP

MCP HTTP 地址：

`POST /mcp`

认证：

```text
Authorization: Bearer <run_asset_mcp_token>
```

支持 JSON-RPC 方法：

- `initialize`
- `tools/list`
- `tools/call`
- `ping`

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
- `report_progress`

### MCP Token

`/mcp` 需要 bearer token：

```text
Authorization: Bearer <ASSET_MCP_TOKEN>
```

token 是 run 级临时凭证，用来把 MCP 请求绑定到某个 `run_id`，从而限制可访问的候选资产、conversation assets、下载 URL 和上传登记范围。

当前实现会在创建 run 时生成 token，数据库保存 token hash，并在每次 `/mcp` 请求时校验 token 状态和过期时间。默认有效期是 24 小时。

主服务器的 MCP HTTP 地址从项目根目录 `.env` 的 `ASSET_MCP_URL` 读取，默认是 `http://127.0.0.1:8010/mcp`。如果 Codex 机器在同一局域网的另一台设备上，要改成主服务器的局域网地址，例如：

```env
ASSET_MCP_URL=http://192.168.110.73:8010/mcp
```

远端 Codex 不应使用 `codex mcp add` 做每次任务配置。当前 runner 会用 `codex exec -c ...` 临时注入 MCP：

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

仍待补齐：token rotate/revoke、过期 token 清理、避免在 run metadata/command JSON 中长期保存明文 token。

## 运行

启动已有 conversation 的 run：

`POST /api/conversations/{conversation_id}/runs`

```json
{
  "base_version_id": null,
  "user_instruction": "请整理字段名",
  "attachment_ids": ["file_xxx"]
}
```

列出 conversation 的 run：

`GET /api/conversations/{conversation_id}/runs`

一键上传并运行：

`POST /api/conversations/run`

这是 multipart 一次性提交接口。它会创建或复用默认用户当前 conversation，上传文件，把上传文件 id 作为新 user message 的附件，然后启动 run。

## 事件

读取 conversation 事件：

`GET /api/conversations/{conversation_id}/events`

前端测试 UI 通过这个接口轮询运行状态、平台事件、Codex stdout/stderr 摘要和最终结果状态。

## 设备

列出设备：

`GET /api/devices`

创建设备：

`POST /api/devices`

更新设备：

`PATCH /api/devices/{device_id}`

删除设备：

`DELETE /api/devices/{device_id}`

检查设备健康：

`POST /api/devices/{device_id}/health-check`

SSH 设备的 `codex_executable` 必须是远端绝对路径。

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

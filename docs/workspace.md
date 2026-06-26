# 工作区

当前项目里有两类 workspace。

## 对话工作区

对话工作区是 conversation 级的兼容目录：

```text
data/workspaces/{user_id}/{conversation_id}/
```

它主要用于保存控制文件和兼容旧链路，不再是长期 durable storage。真正的文件资产以对象存储和 SQLite metadata 为准。

## 会话工作区

每次 run 会创建一个一次性 session workspace：

```text
data/sessions/{session_id}/
  materials/
  guidance/
  outputs/
  logs/
  versions/
  .gateway/
```

当前实现会把本次 run 选中的业务材料放到 `materials/`，把后端管理的 SOP、规则、skill 等文件放到 `guidance/`，Codex 生成的结果写入 `outputs/`。

Session 记录在 `run_sessions` 表中，包含：

- `session_id`
- `run_id`
- `conversation_id`
- `user_id`
- `device_id`
- `root_path`
- `status`
- `expires_at`
- `manifest_json`

过期 session 可以清理：

```powershell
python scripts/clean_expired_sessions.py
```

## 路径安全

所有 workspace 内路径都通过 `pathlib` 和 `safe_join()` 解析，防止路径逃逸：

```text
apps/api/src/gateway/services/path_security.py
```

平台不会接受 `../`、绝对路径，或任何逃出 workspace 根目录的目标路径。

## 长期方向

Session workspace 只应该承载本次执行需要的小控制文件、临时文件和输出目录。大文件数据面应迁移到 OSS signed URL 和按需下载/上传，不应该长期依赖主服务器通过 SFTP 搬完整 workspace。

# 需求范围

本文记录当前仓库已经覆盖的 MVP 能力和暂缓能力。现在的实现已经超过早期里程碑 1-3，加入了对象存储、资产登记、临时 session workspace 和设备管理。

## 已实现

- FastAPI 启动和健康检查。
- SQLite schema 初始化和轻量迁移。
- 默认用户 conversation 创建。
- 上传文件到 S3 兼容对象存储。
- `assets` / `file_assets` durable asset 登记。
- 上传文件区分 `material` 和 `guidance`。
- 单独上传也会创建一条带附件的 user message。
- 同名不同内容上传会替换旧文件记录，避免后续 run 继续选到旧文件。
- 每次 run 创建临时 session workspace。
- 通过 `FilePlanner` / `DistributionService` 生成本次 run 的文件分发计划。
- guidance 文件作为后端管理规则文件参与 run。
- `codex_runs`、`run_sessions`、`run_assets` 等运行记录。
- 本地和 SSH Codex 设备管理。
- 设备路径存 SQLite，不从 `.env` 预读或 seed。
- `codex exec` 命令组装，包含 workspace sandbox 参数。
- Codex stdout/stderr 和 JSONL 事件记录。
- run 完成后扫描 `outputs/` 并登记输出资产。

## 暂缓

- 远程 Codex 机器直接通过 OSS signed URL 下载输入文件。
- Codex 机器直接通过 signed URL 上传输出 artifact。
- 基础 MCP 工具：`search_assets`、`get_asset_download_url`、`get_artifact_upload_url`、`complete_artifact` 等。
- 更强的 FilePlanner 智能检索和面向 Word/PDF/Excel 派生物的 chunk 级读取。
- 派生物生成流水线，例如 extracted text、chunk、archive manifest、preview。
- 输出验收报告。
- 版本恢复和基于版本继续运行。
- 完整产品级前端。

## 当前取舍

当前远程 run 仍会通过 SSH/SFTP 同步 session workspace，这是 MVP 过渡路径。后续架构方向见：

```text
docs/architecture-data-plane.md
```

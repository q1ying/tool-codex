from __future__ import annotations

from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  conversation_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  workspace_id TEXT,
  active_version_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  run_id TEXT,
  created_at TEXT NOT NULL,
  attachments_json TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
  workspace_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  root_path TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  policy_json TEXT
);

CREATE TABLE IF NOT EXISTS file_assets (
  file_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  stored_filename TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  mime_type TEXT,
  size_bytes INTEGER,
  sha256 TEXT,
  created_at TEXT NOT NULL,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  project_id TEXT,
  conversation_id TEXT,
  run_id TEXT,
  source_message_id TEXT,
  original_filename TEXT NOT NULL,
  stored_filename TEXT NOT NULL,
  mime_type TEXT,
  ext TEXT,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  storage_backend TEXT NOT NULL,
  bucket TEXT,
  object_key TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  accepted_at TEXT,
  deleted_at TEXT,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS asset_links (
  link_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  project_id TEXT,
  conversation_id TEXT,
  message_id TEXT,
  run_id TEXT,
  created_at TEXT NOT NULL,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS asset_derivatives (
  derivative_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  derivative_type TEXT NOT NULL,
  storage_backend TEXT NOT NULL,
  bucket TEXT,
  object_key TEXT NOT NULL,
  mime_type TEXT,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS asset_branches (
  branch_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  project_id TEXT,
  conversation_id TEXT,
  branch_key TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  latest_asset_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS asset_lineage (
  parent_asset_id TEXT NOT NULL,
  child_asset_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  run_id TEXT,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  PRIMARY KEY (parent_asset_id, child_asset_id, relation, run_id)
);

CREATE TABLE IF NOT EXISTS run_assets (
  run_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  usage_type TEXT NOT NULL,
  local_path TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  PRIMARY KEY (run_id, asset_id, usage_type)
);

CREATE TABLE IF NOT EXISTS codex_runs (
  run_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  command_json TEXT,
  prompt_path TEXT,
  final_message_path TEXT,
  jsonl_log_path TEXT,
  exit_code INTEGER,
  error_message TEXT,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS run_sessions (
  session_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  device_id TEXT,
  root_path TEXT NOT NULL,
  status TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  manifest_json TEXT,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS codex_devices (
  device_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  runner_mode TEXT NOT NULL,
  status TEXT NOT NULL,
  host TEXT,
  user TEXT,
  port INTEGER,
  remote_root TEXT,
  codex_executable TEXT,
  local_executable TEXT,
  ssh_identity_file TEXT,
  ssh_auth_method TEXT,
  ssh_password TEXT,
  ssh_command_prefix TEXT,
  ssh_strict_host_key_checking TEXT,
  sandbox_mode TEXT,
  disable_external_mcps INTEGER NOT NULL DEFAULT 1,
  config_overrides_json TEXT,
  max_concurrent_runs INTEGER NOT NULL DEFAULT 1,
  weight INTEGER NOT NULL DEFAULT 100,
  last_check_at TEXT,
  last_check_status TEXT,
  last_check_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  run_id TEXT,
  type TEXT NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_file_assets_conversation_sha
ON file_assets(conversation_id, sha256);

CREATE INDEX IF NOT EXISTS idx_file_assets_conversation_original
ON file_assets(conversation_id, original_filename);

CREATE INDEX IF NOT EXISTS idx_assets_owner_sha
ON assets(owner_user_id, sha256);

CREATE INDEX IF NOT EXISTS idx_assets_conversation
ON assets(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_asset_links_asset
ON asset_links(asset_id);

CREATE INDEX IF NOT EXISTS idx_asset_links_conversation
ON asset_links(conversation_id, relation_type);

CREATE INDEX IF NOT EXISTS idx_asset_derivatives_asset_type
ON asset_derivatives(asset_id, derivative_type, status);

CREATE INDEX IF NOT EXISTS idx_asset_branches_lookup
ON asset_branches(owner_user_id, conversation_id, branch_key, asset_type);

CREATE INDEX IF NOT EXISTS idx_asset_lineage_child
ON asset_lineage(child_asset_id, relation);

CREATE INDEX IF NOT EXISTS idx_run_assets_run
ON run_assets(run_id, usage_type);

CREATE INDEX IF NOT EXISTS idx_run_sessions_run
ON run_sessions(run_id);

CREATE INDEX IF NOT EXISTS idx_run_sessions_expires
ON run_sessions(expires_at);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(database_path: Path) -> None:
    with connect(database_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "assets", "branch_id", "TEXT")
        _ensure_column(conn, "assets", "branch_key", "TEXT")
        _ensure_column(conn, "assets", "version_no", "INTEGER")
        _ensure_column(conn, "assets", "role", "TEXT")
        _ensure_column(conn, "assets", "kind", "TEXT")
        _ensure_column(conn, "assets", "run_id", "TEXT")
        _ensure_column(conn, "assets", "accepted_at", "TEXT")
        _ensure_column(conn, "assets", "deleted_at", "TEXT")
        _ensure_column(conn, "asset_derivatives", "sheet_name", "TEXT")
        _ensure_column(conn, "asset_derivatives", "row_count", "INTEGER")
        _ensure_column(conn, "asset_derivatives", "col_count", "INTEGER")
        _ensure_column(conn, "asset_derivatives", "columns_json", "TEXT")
        _ensure_column(conn, "asset_derivatives", "preview_json", "TEXT")
        _ensure_column(conn, "codex_devices", "sandbox_mode", "TEXT")
        _ensure_column(conn, "codex_devices", "disable_external_mcps", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "codex_devices", "config_overrides_json", "TEXT")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

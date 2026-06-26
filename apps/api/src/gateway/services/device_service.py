from __future__ import annotations

import json
from pathlib import PurePath, PureWindowsPath
import sqlite3
from typing import Any

from ..config import Settings
from .ids import now_iso, short_id


PUBLIC_FIELDS = (
    "device_id",
    "name",
    "runner_mode",
    "status",
    "host",
    "user",
    "port",
    "remote_root",
    "codex_executable",
    "local_executable",
    "ssh_identity_file",
    "ssh_auth_method",
    "ssh_command_prefix",
    "ssh_strict_host_key_checking",
    "sandbox_mode",
    "disable_external_mcps",
    "config_overrides",
    "max_concurrent_runs",
    "weight",
    "last_check_at",
    "last_check_status",
    "last_check_message",
    "created_at",
    "updated_at",
)


class DeviceService:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings

    def list(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM codex_devices ORDER BY rowid").fetchall()
        return [self._public(row) for row in rows]

    def get(self, device_id: str, *, include_secret: bool = False) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM codex_devices WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            raise KeyError(device_id)
        return self._full(row) if include_secret else self._public(row)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        device_id = payload.get("device_id") or short_id("device")
        row = self.conn.execute("SELECT device_id FROM codex_devices WHERE device_id = ?", (device_id,)).fetchone()
        if row is not None:
            raise ValueError(f"Device already exists: {device_id}")
        normalized = self._normalize(payload)
        normalized.update(
            {
                "device_id": device_id,
                "created_at": now,
                "updated_at": now,
                "metadata_json": json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
            }
        )
        self._insert(normalized)
        self.conn.commit()
        return self.get(device_id)

    def update(self, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get(device_id, include_secret=True)
        merged = {**current, **payload}
        normalized = self._normalize(merged)
        normalized["updated_at"] = now_iso()
        normalized["metadata_json"] = json.dumps(payload.get("metadata", current.get("metadata", {})) or {}, ensure_ascii=False)
        self.conn.execute(
            """
            UPDATE codex_devices
            SET name = ?, runner_mode = ?, status = ?, host = ?, user = ?, port = ?,
                remote_root = ?, codex_executable = ?, local_executable = ?,
                ssh_identity_file = ?, ssh_auth_method = ?, ssh_password = ?,
                ssh_command_prefix = ?, ssh_strict_host_key_checking = ?,
                sandbox_mode = ?, disable_external_mcps = ?, config_overrides_json = ?,
                max_concurrent_runs = ?, weight = ?, updated_at = ?, metadata_json = ?
            WHERE device_id = ?
            """,
            (
                normalized["name"],
                normalized["runner_mode"],
                normalized["status"],
                normalized["host"],
                normalized["user"],
                normalized["port"],
                normalized["remote_root"],
                normalized["codex_executable"],
                normalized["local_executable"],
                normalized["ssh_identity_file"],
                normalized["ssh_auth_method"],
                normalized["ssh_password"],
                normalized["ssh_command_prefix"],
                normalized["ssh_strict_host_key_checking"],
                normalized["sandbox_mode"],
                1 if normalized["disable_external_mcps"] else 0,
                json.dumps(normalized["config_overrides"], ensure_ascii=False),
                normalized["max_concurrent_runs"],
                normalized["weight"],
                normalized["updated_at"],
                normalized["metadata_json"],
                device_id,
            ),
        )
        self.conn.commit()
        return self.get(device_id)

    def delete(self, device_id: str) -> None:
        result = self.conn.execute("DELETE FROM codex_devices WHERE device_id = ?", (device_id,))
        if result.rowcount == 0:
            raise KeyError(device_id)
        self.conn.commit()

    def record_health(self, device_id: str, status: str, message: str) -> None:
        self.conn.execute(
            """
            UPDATE codex_devices
            SET last_check_at = ?, last_check_status = ?, last_check_message = ?, updated_at = ?
            WHERE device_id = ?
            """,
            (now_iso(), status, message, now_iso(), device_id),
        )
        self.conn.commit()

    def select_for_run(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT * FROM codex_devices
            WHERE status = 'enabled'
              AND COALESCE(last_check_status, 'ok') != 'failed'
            ORDER BY rowid
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("No enabled Codex device is available.")
        return self._full(row)

    def _insert(self, payload: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO codex_devices
            (device_id, name, runner_mode, status, host, user, port, remote_root,
             codex_executable, local_executable, ssh_identity_file, ssh_auth_method,
             ssh_password, ssh_command_prefix, ssh_strict_host_key_checking,
             sandbox_mode, disable_external_mcps, config_overrides_json,
             max_concurrent_runs, weight, last_check_at, last_check_status,
             last_check_message, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["device_id"],
                payload["name"],
                payload["runner_mode"],
                payload["status"],
                payload["host"],
                payload["user"],
                payload["port"],
                payload["remote_root"],
                payload["codex_executable"],
                payload["local_executable"],
                payload["ssh_identity_file"],
                payload["ssh_auth_method"],
                payload["ssh_password"],
                payload["ssh_command_prefix"],
                payload["ssh_strict_host_key_checking"],
                payload["sandbox_mode"],
                1 if payload["disable_external_mcps"] else 0,
                json.dumps(payload["config_overrides"], ensure_ascii=False),
                payload["max_concurrent_runs"],
                payload["weight"],
                payload.get("last_check_at"),
                payload.get("last_check_status"),
                payload.get("last_check_message"),
                payload["created_at"],
                payload["updated_at"],
                payload["metadata_json"],
            ),
        )

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        runner_mode = str(payload.get("runner_mode") or "local")
        if runner_mode not in {"local", "ssh"}:
            raise ValueError("runner_mode must be local or ssh.")
        status = str(payload.get("status") or "enabled")
        if status not in {"enabled", "disabled"}:
            raise ValueError("status must be enabled or disabled.")
        auth_method = str(payload.get("ssh_auth_method") or "key")
        if auth_method not in {"key", "password"}:
            raise ValueError("ssh_auth_method must be key or password.")
        sandbox_mode = str(payload.get("sandbox_mode") or self.settings.codex_sandbox_mode or "workspace-write")
        if sandbox_mode not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError("sandbox_mode must be read-only, workspace-write, or danger-full-access.")
        config_overrides = payload.get("config_overrides")
        if config_overrides is None:
            config_overrides = self._parse_config_overrides(payload.get("config_overrides_json"))
        return {
            "name": str(payload.get("name") or "Codex Device"),
            "runner_mode": runner_mode,
            "status": status,
            "host": str(payload.get("host") or ""),
            "user": str(payload.get("user") or ""),
            "port": int(payload.get("port") or 22),
            "remote_root": str(payload.get("remote_root") or "~/lqy/codex-workspaces"),
            "codex_executable": self._normalize_executable(
                runner_mode=runner_mode,
                value=str(payload.get("codex_executable") or ""),
                field="codex_executable",
            ),
            "local_executable": self._normalize_executable(
                runner_mode=runner_mode,
                value=str(payload.get("local_executable") or ""),
                field="local_executable",
            ),
            "ssh_identity_file": str(payload.get("ssh_identity_file") or ""),
            "ssh_auth_method": auth_method,
            "ssh_password": str(payload.get("ssh_password") or ""),
            "ssh_command_prefix": str(payload.get("ssh_command_prefix") or ""),
            "ssh_strict_host_key_checking": str(payload.get("ssh_strict_host_key_checking") or "yes"),
            "sandbox_mode": sandbox_mode,
            "disable_external_mcps": bool(payload.get("disable_external_mcps", self.settings.codex_disable_external_mcps)),
            "config_overrides": self._normalize_config_overrides(config_overrides),
            "max_concurrent_runs": int(payload.get("max_concurrent_runs") or 1),
            "weight": int(payload.get("weight") or 100),
        }

    def _normalize_executable(self, *, runner_mode: str, value: str, field: str) -> str:
        text = value.strip()
        if runner_mode == "ssh" and field == "local_executable":
            return text
        if runner_mode == "local" and field == "codex_executable":
            return text
        if not text:
            raise ValueError(f"{field} is required for {runner_mode} devices.")
        if runner_mode == "ssh" and field == "codex_executable" and not text.startswith("/"):
            raise ValueError("codex_executable must be an absolute remote path for ssh devices, for example /home/openclaw/.local/bin/codex.")
        if runner_mode == "local" and field == "local_executable" and not (PureWindowsPath(text).is_absolute() or PurePath(text).is_absolute()):
            raise ValueError("local_executable must be an absolute local path for local devices.")
        return text

    def _public(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = {key: self._read_optional(row, key) for key in PUBLIC_FIELDS}
        data["disable_external_mcps"] = bool(data["disable_external_mcps"])
        data["config_overrides"] = self._parse_config_overrides(data.get("config_overrides"))
        data["metadata"] = json.loads(row["metadata_json"] or "{}")
        data["has_ssh_password"] = bool(row["ssh_password"])
        return data

    def _full(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row) if isinstance(row, sqlite3.Row) else dict(row)
        data["sandbox_mode"] = data.get("sandbox_mode") or self.settings.codex_sandbox_mode
        data["disable_external_mcps"] = bool(data.get("disable_external_mcps", self.settings.codex_disable_external_mcps))
        data["config_overrides"] = self._parse_config_overrides(data.get("config_overrides_json"))
        data["metadata"] = json.loads(data.get("metadata_json") or "{}")
        return data

    def _read_optional(self, row: sqlite3.Row | dict[str, Any], key: str) -> Any:
        if key == "config_overrides":
            return self._parse_config_overrides(row["config_overrides_json"] if "config_overrides_json" in row.keys() else None)
        try:
            value = row[key]
        except (KeyError, IndexError):
            value = None
        if key == "sandbox_mode":
            return value or self.settings.codex_sandbox_mode
        if key == "disable_external_mcps":
            return self.settings.codex_disable_external_mcps if value is None else bool(value)
        return value

    def _parse_config_overrides(self, raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return self._normalize_config_overrides(raw)
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = [line.strip() for line in text.splitlines()]
            return self._normalize_config_overrides(parsed)
        return []

    def _normalize_config_overrides(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("config_overrides must be a list.")
        normalized = []
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            if "=" not in text:
                raise ValueError("each config override must look like key=value.")
            normalized.append(text)
        return normalized


def device_snapshot(device: dict[str, Any]) -> dict[str, Any]:
    return {key: device.get(key) for key in PUBLIC_FIELDS if key not in {"created_at", "updated_at"}}

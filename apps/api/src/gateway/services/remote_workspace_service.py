from __future__ import annotations

import shlex
import subprocess
from typing import Any


def cleanup_remote_sessions(device: dict[str, Any], *, older_than_minutes: int = 60) -> dict[str, Any]:
    if device.get("runner_mode") != "ssh":
        raise ValueError("Only ssh devices have remote workspaces to clean.")
    if older_than_minutes < 1:
        raise ValueError("older_than_minutes must be at least 1.")

    command = _cleanup_command(str(device.get("remote_root") or ""), older_than_minutes)
    prefix = str(device.get("ssh_command_prefix") or "").strip()
    if prefix:
        command = f"{prefix} {shlex.quote(command)}"

    if device.get("ssh_auth_method") == "password" or device.get("ssh_password"):
        return _run_with_paramiko(device, command)
    return _run_with_openssh(device, command)


def cleanup_remote_run_workspace(
    device: dict[str, Any],
    *,
    session_id: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Reserved entrypoint for per-run remote workspace cleanup.

    TODO:
    - Implement explicit deletion of one remote session workspace after the new
      OSS/MCP data plane is fully active.
    - Use Paramiko/OpenSSH through the same helpers as cleanup_remote_sessions.
    - Add cache policy inputs later: ttl, max_bytes, min_hit_count,
      last_accessed_at, and LRU ordering.
    - Keep destructive deletion opt-in until remote cache hit behavior is
      observable.
    """
    if device.get("runner_mode") != "ssh":
        raise ValueError("Only ssh devices have remote workspaces to clean.")
    return {
        "status": "planned",
        "dry_run": dry_run,
        "session_id": session_id,
        "message": "Per-run remote workspace cleanup is reserved but not implemented yet.",
        "future_policy": {
            "ttl": True,
            "lru": True,
            "cache_hit_rate": True,
            "max_bytes": True,
        },
    }


def _cleanup_command(remote_root: str, older_than_minutes: int) -> str:
    root_expr = _remote_root_expr(remote_root)
    return " ; ".join(
        [
            f"root={root_expr}",
            'if [ -z "$root" ] || [ "$root" = "/" ] || [ "$root" = "$HOME" ]; then echo "__refuse_unsafe_root__ $root" >&2; exit 2; fi',
            'if [ ! -d "$root" ]; then echo "__root_missing__ $root"; exit 0; fi',
            f'find "$root" -mindepth 1 -maxdepth 1 -type d -name \'sess_*\' -mmin +{older_than_minutes} -print -exec rm -rf -- {{}} +',
        ]
    )


def _remote_root_expr(remote_root: str) -> str:
    root = remote_root.strip()
    if not root:
        raise ValueError("remote_root is required.")
    if root == "~":
        return "$HOME"
    if root.startswith("~/"):
        return "$HOME" + shlex.quote("/" + root[2:])
    if root.startswith("/"):
        return shlex.quote(root)
    raise ValueError("remote_root must be an absolute path or start with ~/.")


def _run_with_openssh(device: dict[str, Any], command: str) -> dict[str, Any]:
    cmd = ["ssh", "-p", str(device.get("port") or 22)]
    if device.get("ssh_identity_file"):
        cmd.extend(["-i", str(device.get("ssh_identity_file"))])
    cmd.extend(["-o", f"StrictHostKeyChecking={device.get('ssh_strict_host_key_checking') or 'yes'}"])
    cmd.extend([f"{device.get('user')}@{device.get('host')}", command])
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except Exception as exc:
        return {
            "status": "failed",
            "message": str(exc),
            "stdout": "",
            "stderr": "",
            "deleted": [],
        }
    return _format_result(completed.returncode, completed.stdout, completed.stderr)


def _run_with_paramiko(device: dict[str, Any], command: str) -> dict[str, Any]:
    try:
        import paramiko
    except ImportError as exc:
        raise ValueError("Password SSH requires paramiko. Install it with: pip install paramiko") from exc

    client = paramiko.SSHClient()
    if str(device.get("ssh_strict_host_key_checking") or "yes").lower() in {"no", "false", "0"}:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        client.connect(
            hostname=str(device.get("host") or ""),
            port=int(device.get("port") or 22),
            username=str(device.get("user") or ""),
            password=str(device.get("ssh_password") or ""),
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
        )
        _, stdout, stderr = client.exec_command(command, timeout=60)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return _format_result(exit_code, out, err)
    except Exception as exc:
        return {
            "status": "failed",
            "message": str(exc),
            "stdout": "",
            "stderr": "",
            "deleted": [],
        }
    finally:
        client.close()


def _format_result(exit_code: int, stdout: str, stderr: str) -> dict[str, Any]:
    deleted = [line.strip() for line in stdout.splitlines() if line.strip() and not line.startswith("__root_missing__")]
    return {
        "status": "ok" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "stdout": stdout,
        "stderr": stderr,
        "message": f"Deleted {len(deleted)} expired remote session workspace(s)." if exit_code == 0 else "Remote cleanup failed.",
    }

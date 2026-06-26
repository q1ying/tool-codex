from __future__ import annotations

from pathlib import Path, PurePosixPath
import platform
import shlex
import subprocess
from typing import Any

from ..config import Settings


def check_remote_codex(settings: Settings) -> dict[str, Any]:
    return {
        "status": "skipped",
        "message": "Device checks are stored in SQLite. Use POST /api/devices/{device_id}/health-check.",
    }


def check_device_codex(device: dict[str, Any]) -> dict[str, Any]:
    if device.get("runner_mode") == "local":
        return _check_local(device)
    if device.get("runner_mode") != "ssh":
        return {
            "status": "skipped",
            "runner_mode": device.get("runner_mode"),
            "message": "Device runner_mode is not ssh.",
        }
    if not device.get("host") or not device.get("user"):
        return {
            "status": "failed",
            "runner_mode": device.get("runner_mode"),
            "message": "Device host and user are required.",
        }

    codex_executable = str(device.get("codex_executable") or "")
    if not codex_executable.startswith("/"):
        return {
            "status": "failed",
            "runner_mode": device.get("runner_mode"),
            "message": "SSH codex_executable must be an absolute remote path, for example /home/openclaw/.local/bin/codex.",
        }
    command = _diagnostic_command(codex_executable)
    command = _wrap_with_prefix(command, str(device.get("ssh_command_prefix") or ""))

    if device.get("ssh_auth_method") == "password" or device.get("ssh_password"):
        return _check_with_paramiko(device, command)
    return _check_with_openssh(device, command)


def _check_local(device: dict[str, Any]) -> dict[str, Any]:
    executable = str(device.get("local_executable") or "")
    if not executable or not (Path(executable).is_absolute() or platform.system().lower() == "windows" and Path(executable).drive):
        return {
            "status": "failed",
            "runner_mode": "local",
            "local_executable": executable,
            "message": "local_executable must be an absolute local path.",
        }
    sandbox_problem = _local_sandbox_problem(device)
    try:
        completed = subprocess.run([executable, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    except Exception as exc:
        return {
            "status": "failed",
            "runner_mode": "local",
            "local_executable": executable,
            "message": str(exc),
        }
    ok = completed.returncode == 0
    if ok and sandbox_problem:
        return {
            "status": "failed",
            "runner_mode": "local",
            "local_executable": executable,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "message": sandbox_problem,
        }
    return {
        "status": "ok" if ok else "failed",
        "runner_mode": "local",
        "local_executable": executable,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "message": "Local Codex executable is available." if ok else "Local Codex executable check failed.",
    }


def _local_sandbox_problem(device: dict[str, Any]) -> str:
    sandbox_mode = str(device.get("sandbox_mode") or "workspace-write")
    if sandbox_mode != "workspace-write":
        return ""
    if platform.system().lower() != "windows":
        return ""
    helper = Path.home() / ".codex" / ".sandbox-bin" / "codex-windows-sandbox-setup.exe"
    if helper.exists():
        return ""
    return (
        "Local Windows device uses workspace-write, but codex-windows-sandbox-setup.exe is missing. "
        "Install/repair the Codex Windows sandbox helper, or set this trusted local device to danger-full-access."
    )


def _diagnostic_command(codex_executable: str) -> str:
    executable = shlex.quote(codex_executable)
    path_prefix = _executable_path_prefix(codex_executable)
    return " ; ".join(
        [
            f"export PATH={path_prefix}:$PATH",
            "echo __ssh_ok__",
            "echo SHELL=$SHELL",
            "echo PATH=$PATH",
            f"if [ -x {executable} ]; then echo CODEX_PATH={executable}; else echo CODEX_PATH=; fi",
            f"{executable} --version",
        ]
    )


def _executable_path_prefix(codex_executable: str) -> str:
    executable_dir = PurePosixPath(codex_executable).parent
    prefixes = [str(executable_dir)]
    if executable_dir.name == "bin":
        prefixes.append(str(executable_dir.parent / "sbin"))
    return ":".join(shlex.quote(item) for item in prefixes)


def _wrap_with_prefix(command: str, prefix: str) -> str:
    prefix = prefix.strip()
    if not prefix:
        return command
    return f"{prefix} {shlex.quote(command)}"


def _check_with_paramiko(device: dict[str, Any], command: str) -> dict[str, Any]:
    try:
        import paramiko
    except ImportError:
        return {
            "status": "failed",
            "runner_mode": "ssh",
            "auth_method": "password",
            "message": "Password SSH requires paramiko. Install it with: pip install paramiko",
        }

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
        _, stdout, stderr = client.exec_command(command, timeout=30)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return _format_result(device, "password", exit_code, out, err)
    except Exception as exc:
        return {
            "status": "failed",
            "runner_mode": "ssh",
            "auth_method": "password",
            "host": device.get("host"),
            "user": device.get("user"),
            "message": str(exc),
        }
    finally:
        client.close()


def _check_with_openssh(device: dict[str, Any], command: str) -> dict[str, Any]:
    cmd = ["ssh", "-p", str(device.get("port") or 22)]
    if device.get("ssh_identity_file"):
        cmd.extend(["-i", str(device.get("ssh_identity_file"))])
    cmd.extend(["-o", f"StrictHostKeyChecking={device.get('ssh_strict_host_key_checking') or 'yes'}"])
    cmd.extend([f"{device.get('user')}@{device.get('host')}", command])
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except Exception as exc:
        return {
            "status": "failed",
            "runner_mode": "ssh",
            "auth_method": "key",
            "host": device.get("host"),
            "user": device.get("user"),
            "message": str(exc),
        }
    return _format_result(device, "key", completed.returncode, completed.stdout, completed.stderr)


def _format_result(device: dict[str, Any], auth_method: str, exit_code: int, stdout: str, stderr: str) -> dict[str, Any]:
    codex_path = ""
    for line in stdout.splitlines():
        if line.startswith("CODEX_PATH="):
            codex_path = line.split("=", 1)[1].strip()
            break
    ok = exit_code == 0 and bool(codex_path)
    message = "Remote SSH and codex are available." if ok else "SSH connected, but remote codex check failed."
    if "command not found" in stderr or not codex_path:
        message = "SSH connected, but codex_executable was not found or is not executable at the configured absolute path."
    return {
        "status": "ok" if ok else "failed",
        "runner_mode": "ssh",
        "auth_method": auth_method,
        "host": device.get("host"),
        "user": device.get("user"),
        "port": device.get("port"),
        "remote_root": device.get("remote_root"),
        "codex_executable": device.get("codex_executable"),
        "command_prefix": device.get("ssh_command_prefix"),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "message": message,
    }

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import posixpath
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class CodexRunRequest:
    run_id: str
    conversation_id: str
    workspace_path: Path
    prompt_text: str
    final_message_path: Path
    jsonl_log_path: Path
    executable: str = "codex"
    timeout_seconds: int = 900
    runner_mode: str = "local"
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_port: int = 22
    ssh_remote_root: str = "~/lqy/codex-workspaces"
    ssh_executable: str = "codex"
    ssh_identity_file: str = ""
    ssh_auth_method: str = "key"
    ssh_password: str = ""
    ssh_command_prefix: str = ""
    ssh_strict_host_key_checking: str = "yes"
    sandbox_mode: str = "workspace-write"
    disable_external_mcps: bool = True
    config_overrides: tuple[str, ...] = ()
    asset_mcp_token: str = ""
    asset_mcp_url: str = ""
    asset_mcp_enabled_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodexRunResult:
    exit_code: int | None
    status: str
    error_message: str | None = None


EventCallback = Callable[[str, str, dict], None]


class CodexRunner:
    def build_command(self, job: CodexRunRequest) -> list[str]:
        cmd = [
            job.executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
        ]
        cmd.extend(
            [
            "--sandbox",
            job.sandbox_mode,
            "-C",
            str(job.workspace_path),
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
            str(job.final_message_path),
            ]
        )
        cmd.extend(_codex_config_overrides(job))
        cmd.append("-")
        return cmd

    def run(self, job: CodexRunRequest, on_event: EventCallback | None = None) -> CodexRunResult:
        if job.runner_mode == "ssh":
            return SshCodexRunner().run(job, on_event)
        if job.runner_mode != "local":
            return CodexRunResult(exit_code=None, status="failed", error_message=f"Unsupported runner mode: {job.runner_mode}")
        job.final_message_path.parent.mkdir(parents=True, exist_ok=True)
        job.jsonl_log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self.build_command(job)

        try:
            process = subprocess.Popen(
                cmd,
                cwd=job.workspace_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(job),
            )
        except FileNotFoundError as exc:
            return CodexRunResult(exit_code=None, status="failed", error_message=str(exc))

        return _stream_process(
            process,
            timeout_seconds=job.timeout_seconds,
            jsonl_log_path=job.jsonl_log_path,
            on_event=on_event,
            default_event_type="codex_json_event",
            stdin_text=job.prompt_text,
            heartbeat_event_type="codex_running",
        )


class SshCodexRunner:
    def run(self, job: CodexRunRequest, on_event: EventCallback | None = None) -> CodexRunResult:
        if job.ssh_auth_method == "password" or job.ssh_password:
            return ParamikoPasswordCodexRunner().run(job, on_event)
        if not job.ssh_host or not job.ssh_user:
            return CodexRunResult(
                exit_code=None,
                status="failed",
                error_message="CODEX_SSH_HOST and CODEX_SSH_USER are required for CODEX_RUNNER_MODE=ssh.",
            )

        job.final_message_path.parent.mkdir(parents=True, exist_ok=True)
        job.jsonl_log_path.parent.mkdir(parents=True, exist_ok=True)
        remote_workspace = _remote_join(job.ssh_remote_root, job.conversation_id)
        remote_final = _remote_join(remote_workspace, ".gateway", "run_final.md")

        setup = self._run_simple(
            self._ssh_cmd(job, f"mkdir -p {_remote_shell_path(remote_workspace)} {_remote_shell_path(_remote_join(remote_workspace, '.gateway'))} {_remote_shell_path(_remote_join(remote_workspace, 'outputs'))} {_remote_shell_path(_remote_join(remote_workspace, 'logs'))}"),
            "ssh_setup",
            on_event,
            job.jsonl_log_path,
        )
        if setup.status != "completed":
            return setup

        remote_cmd = self._remote_codex_command(job, remote_workspace, remote_final)
        if on_event:
            on_event("codex_remote_started", "Remote Codex run started.", {"remote_workspace": remote_workspace})
        run_result = self._run_streaming(
            self._ssh_cmd(job, _wrap_remote_command(job, remote_cmd)),
            cwd=job.workspace_path,
            timeout_seconds=job.timeout_seconds,
            jsonl_log_path=job.jsonl_log_path,
            on_event=on_event,
            stdin_text=job.prompt_text,
            heartbeat_event_type="remote_codex_running",
        )

        remote_final_fetch = self._run_simple(
            self._scp_file_download_cmd(job, remote_final, job.final_message_path),
            "ssh_final_download",
            on_event,
            job.jsonl_log_path,
            allow_failure=True,
        )
        if remote_final_fetch.status != "completed" and job.final_message_path.exists() is False:
            local_output_final = job.workspace_path / "outputs" / "final.md"
            if local_output_final.exists():
                job.final_message_path.write_text(local_output_final.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

        return run_result

    def _remote_codex_command(self, job: CodexRunRequest, remote_workspace: str, remote_final: str) -> str:
        parts = [
            job.ssh_executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
        ]
        parts.extend(
            [
            "--sandbox",
            job.sandbox_mode,
            "-C",
            _remote_shell_path(remote_workspace),
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
            _remote_shell_path(remote_final),
            ]
        )
        parts.extend(_codex_config_overrides(job))
        parts.append("-")
        quoted: list[str] = []
        for part in parts:
            text = str(part)
            if text.startswith("$HOME/") or text == "$HOME":
                quoted.append(text)
            else:
                quoted.append(shlex.quote(text))
        command = " ".join(quoted)
        path_prefix = _remote_executable_path_prefix(job.ssh_executable)
        exports = []
        if job.asset_mcp_token:
            exports.append(f"export ASSET_MCP_TOKEN={shlex.quote(job.asset_mcp_token)}")
        if path_prefix:
            exports.append(f"export PATH={path_prefix}:$PATH")
        if exports:
            return "; ".join(exports) + f"; {command}"
        return command

    def _ssh_base(self, job: CodexRunRequest) -> list[str]:
        cmd = ["ssh", "-p", str(job.ssh_port)]
        if job.ssh_identity_file:
            cmd.extend(["-i", job.ssh_identity_file])
        cmd.extend(["-o", f"StrictHostKeyChecking={job.ssh_strict_host_key_checking}"])
        return cmd

    def _scp_base(self, job: CodexRunRequest) -> list[str]:
        cmd = ["scp", "-P", str(job.ssh_port), "-r"]
        if job.ssh_identity_file:
            cmd.extend(["-i", job.ssh_identity_file])
        cmd.extend(["-o", f"StrictHostKeyChecking={job.ssh_strict_host_key_checking}"])
        return cmd

    def _remote_target(self, job: CodexRunRequest, path: str) -> str:
        return f"{job.ssh_user}@{job.ssh_host}:{path}"

    def _ssh_cmd(self, job: CodexRunRequest, remote_command: str) -> list[str]:
        return [*self._ssh_base(job), f"{job.ssh_user}@{job.ssh_host}", remote_command]

    def _scp_file_download_cmd(self, job: CodexRunRequest, remote_file: str, local_file: Path) -> list[str]:
        local_file.parent.mkdir(parents=True, exist_ok=True)
        return [*self._scp_base(job), self._remote_target(job, remote_file), str(local_file)]

    def _run_simple(
        self,
        cmd: list[str],
        event_type: str,
        on_event: EventCallback | None,
        jsonl_log_path: Path,
        *,
        allow_failure: bool = False,
    ) -> CodexRunResult:
        result = self._run_streaming(
            cmd,
            cwd=None,
            timeout_seconds=300,
            jsonl_log_path=jsonl_log_path,
            on_event=on_event,
            event_type=event_type,
        )
        if allow_failure and result.status != "completed":
            return CodexRunResult(exit_code=result.exit_code, status="completed")
        return result


class ParamikoPasswordCodexRunner:
    def run(self, job: CodexRunRequest, on_event: EventCallback | None = None) -> CodexRunResult:
        if not job.ssh_host or not job.ssh_user or not job.ssh_password:
            return CodexRunResult(
                exit_code=None,
                status="failed",
                error_message="CODEX_SSH_HOST, CODEX_SSH_USER, and CODEX_SSH_PASSWORD are required for password SSH.",
            )
        try:
            import paramiko
        except ImportError:
            return CodexRunResult(
                exit_code=None,
                status="failed",
                error_message="Password SSH requires paramiko. Install it with: pip install paramiko",
            )

        job.final_message_path.parent.mkdir(parents=True, exist_ok=True)
        job.jsonl_log_path.parent.mkdir(parents=True, exist_ok=True)
        remote_workspace = _remote_join(job.ssh_remote_root, job.conversation_id)
        remote_final = _remote_join(remote_workspace, ".gateway", "run_final.md")
        client = paramiko.SSHClient()
        if job.ssh_strict_host_key_checking.lower() in {"no", "false", "0"}:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        try:
            client.connect(
                hostname=job.ssh_host,
                port=job.ssh_port,
                username=job.ssh_user,
                password=job.ssh_password,
                look_for_keys=False,
                allow_agent=False,
                timeout=30,
            )
            sftp = client.open_sftp()
            self._mkdir_p(sftp, remote_workspace)
            self._mkdir_p(sftp, _remote_join(remote_workspace, ".gateway"))
            self._mkdir_p(sftp, _remote_join(remote_workspace, "outputs"))
            self._mkdir_p(sftp, _remote_join(remote_workspace, "logs"))
            sftp.close()

            command = _wrap_remote_command(job, SshCodexRunner()._remote_codex_command(job, remote_workspace, remote_final))
            if on_event:
                on_event("codex_remote_started", "Remote Codex run started via password SSH.", {"remote_workspace": remote_workspace})
            result = self._exec_streaming(client, command, job, on_event)

            sftp = client.open_sftp()
            try:
                self._get_file(sftp, remote_final, job.final_message_path)
            except OSError:
                local_output_final = job.workspace_path / "outputs" / "final.md"
                if local_output_final.exists():
                    job.final_message_path.write_text(local_output_final.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            sftp.close()
            return result
        except Exception as exc:
            return CodexRunResult(exit_code=None, status="failed", error_message=str(exc))
        finally:
            client.close()

    def _exec_streaming(self, client, command: str, job: CodexRunRequest, on_event: EventCallback | None) -> CodexRunResult:
        transport = client.get_transport()
        if transport is None:
            return CodexRunResult(exit_code=None, status="failed", error_message="SSH transport is not available.")
        channel = transport.open_session()
        channel.exec_command(command)
        try:
            channel.sendall(job.prompt_text.encode("utf-8"))
            if hasattr(channel, "shutdown_write"):
                channel.shutdown_write()
            else:
                channel.shutdown(1)
        except Exception as exc:
            channel.close()
            return CodexRunResult(exit_code=None, status="failed", error_message=f"Failed to send prompt over SSH stdin: {exc}")
        started = time.monotonic()
        last_output = started
        last_heartbeat = started
        stdout_buffer = ""
        stderr_buffer = ""
        timed_out = False
        with job.jsonl_log_path.open("a", encoding="utf-8") as log:
            while not channel.exit_status_ready():
                if time.monotonic() - started > job.timeout_seconds:
                    timed_out = True
                    channel.close()
                    break
                if channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    last_output = time.monotonic()
                    stdout_buffer = self._consume_buffer(stdout_buffer + chunk, "stdout", log, on_event)
                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    last_output = time.monotonic()
                    stderr_buffer = self._consume_buffer(stderr_buffer + chunk, "stderr", log, on_event)
                now = time.monotonic()
                if on_event and now - last_heartbeat >= 5:
                    on_event(
                        "remote_codex_running",
                        "Remote Codex is still running.",
                        {
                            "elapsed_seconds": int(now - started),
                            "seconds_since_last_output": int(now - last_output),
                        },
                    )
                    last_heartbeat = now
                time.sleep(0.1)
            while channel.recv_ready():
                stdout_buffer = self._consume_buffer(stdout_buffer + channel.recv(4096).decode("utf-8", errors="replace"), "stdout", log, on_event)
            while channel.recv_stderr_ready():
                stderr_buffer = self._consume_buffer(stderr_buffer + channel.recv_stderr(4096).decode("utf-8", errors="replace"), "stderr", log, on_event)
            if stdout_buffer:
                self._emit_line(stdout_buffer, "stdout", log, on_event)
            if stderr_buffer:
                self._emit_line(stderr_buffer, "stderr", log, on_event)
        if timed_out:
            return CodexRunResult(exit_code=None, status="timeout", error_message="Remote Codex run timed out.")
        exit_code = channel.recv_exit_status()
        if exit_code == 0:
            return CodexRunResult(exit_code=0, status="completed")
        return CodexRunResult(exit_code=exit_code, status="failed", error_message=f"Remote Codex exited with {exit_code}.")

    def _consume_buffer(self, buffer: str, stream_name: str, log, on_event: EventCallback | None) -> str:
        lines = buffer.splitlines(keepends=True)
        remainder = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            remainder = lines.pop()
        for raw in lines:
            self._emit_line(raw.rstrip("\r\n"), stream_name, log, on_event)
        return remainder

    def _emit_line(self, line: str, stream_name: str, log, on_event: EventCallback | None) -> None:
        if stream_name == "stdout":
            parsed = _parse_json_line(line)
            log.write(json.dumps(parsed or {"raw": line}, ensure_ascii=False) + "\n")
            log.flush()
            if on_event:
                if parsed:
                    on_event("codex_json_event", _summarize_codex_event(parsed), {"raw": parsed})
                else:
                    on_event("codex_stdout", line, {"line": line})
        else:
            log.write(json.dumps({"stderr": line}, ensure_ascii=False) + "\n")
            log.flush()
            if on_event:
                on_event("codex_stderr", line, {"line": line, "severity": _classify_stderr(line)})

    def _mkdir_p(self, sftp, remote_path: str) -> None:
        normalized = _remote_no_tilde(remote_path).replace("\\", "/")
        absolute = normalized.startswith("/")
        parts = [part for part in normalized.strip("/").split("/") if part]
        current = "/" if absolute else ""
        for part in parts:
            current = posixpath.join(current, part) if current else part
            try:
                sftp.stat(current)
            except OSError:
                sftp.mkdir(current)

    def _get_file(self, sftp, remote_path: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        sftp.get(_remote_no_tilde(remote_path), str(local_path))

    def _run_streaming(
        self,
        cmd: list[str],
        cwd: Path | None,
        timeout_seconds: int,
        jsonl_log_path: Path,
        on_event: EventCallback | None,
        event_type: str = "codex_json_event",
        stdin_text: str | None = None,
        heartbeat_event_type: str | None = None,
    ) -> CodexRunResult:
        try:
            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(job),
            )
        except FileNotFoundError as exc:
            return CodexRunResult(exit_code=None, status="failed", error_message=str(exc))

        return _stream_process(
            process,
            timeout_seconds=timeout_seconds,
            jsonl_log_path=jsonl_log_path,
            on_event=on_event,
            default_event_type=event_type,
            stdin_text=stdin_text,
            heartbeat_event_type=heartbeat_event_type,
        )


def _parse_json_line(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else {"value": value}


def _codex_config_overrides(job: CodexRunRequest) -> list[str]:
    overrides = [
        "shell_environment_policy.inherit=all",
    ]
    if job.disable_external_mcps:
        overrides.extend(
            [
                'plugins."linear@openai-curated".enabled=false',
                'plugins."slack@openai-curated".enabled=false',
                'plugins."github@openai-curated".enabled=false',
                'plugins."browser@openai-bundled".enabled=false',
            ]
        )
    if job.asset_mcp_token and job.asset_mcp_url:
        enabled_tools = job.asset_mcp_enabled_tools or (
            "list_candidate_assets",
            "search_assets",
            "get_asset_summary",
            "read_asset_chunk",
            "get_asset_download_url",
            "get_artifact_upload_url",
            "complete_artifact",
            "report_progress",
        )
        tools_literal = "[" + ",".join(json.dumps(item) for item in enabled_tools) + "]"
        overrides.extend(
            [
                "mcp.remote_mcp_client_enabled=true",
                f"mcp_servers.asset.url={json.dumps(job.asset_mcp_url)}",
                'mcp_servers.asset.bearer_token_env_var="ASSET_MCP_TOKEN"',
                "mcp_servers.asset.required=true",
                f"mcp_servers.asset.enabled_tools={tools_literal}",
            ]
        )
    overrides.extend(job.config_overrides)
    args: list[str] = []
    for override in overrides:
        args.extend(["-c", override])
    return args


def _utf8_env(job: CodexRunRequest | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    if job is not None and job.asset_mcp_token:
        env["ASSET_MCP_TOKEN"] = job.asset_mcp_token
    return env


def _read_lines(stream_name: str, stream, line_queue: queue.Queue[tuple[str, str | None]]) -> None:
    if stream is None:
        line_queue.put((stream_name, None))
        return
    try:
        for line in iter(stream.readline, ""):
            if line == "":
                break
            line_queue.put((stream_name, line.rstrip("\r\n")))
    finally:
        line_queue.put((stream_name, None))


def _summarize_codex_event(event: dict) -> str:
    event_type = event.get("type") or event.get("event") or event.get("kind") or "json"
    item = event.get("item")
    if isinstance(item, dict):
        item_type = item.get("type") or item.get("kind") or "item"
        command = item.get("command") or item.get("cmd")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if isinstance(command, str) and command.strip():
            status = "started" if str(event_type).endswith("started") else "completed" if str(event_type).endswith("completed") else "reported"
            return f"Codex {status} {item_type}: {command[:300]}"
        item_message = item.get("message") or item.get("title") or item.get("text") or item.get("summary")
        if isinstance(item_message, str) and item_message.strip():
            return f"Codex {event_type}: {item_message[:300]}"
    if isinstance(event.get("delta"), str) and event["delta"].strip():
        return f"Codex output: {event['delta'][:300]}"
    message = event.get("message") or event.get("summary") or event.get("text")
    if isinstance(message, str) and message.strip():
        return f"Codex {event_type}: {message[:300]}"
    return f"Codex emitted {event_type}."


def _classify_stderr(line: str) -> str:
    lowered = line.lower()
    if "reading additional input from stdin" in lowered:
        return "info"
    if "warning" in lowered:
        return "warning"
    error_markers = (
        "error",
        "failed",
        "traceback",
        "command not found",
        "no such file",
        "permission denied",
        "timed out",
    )
    if any(marker in lowered for marker in error_markers):
        return "error"
    return "info"


def _stream_process(
    process: subprocess.Popen,
    *,
    timeout_seconds: int,
    jsonl_log_path: Path,
    on_event: EventCallback | None,
    default_event_type: str,
    stdin_text: str | None = None,
    heartbeat_event_type: str | None = None,
) -> CodexRunResult:
    started = time.monotonic()
    last_output = started
    last_heartbeat = started
    line_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    timed_out = False

    stdout_thread = threading.Thread(target=_read_lines, args=("stdout", process.stdout, line_queue), daemon=True)
    stderr_thread = threading.Thread(target=_read_lines, args=("stderr", process.stderr, line_queue), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    if stdin_text is not None and process.stdin:
        try:
            process.stdin.write(stdin_text)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    with jsonl_log_path.open("a", encoding="utf-8") as log:
        while process.poll() is None or stdout_thread.is_alive() or stderr_thread.is_alive() or not line_queue.empty():
            if process.poll() is None and time.monotonic() - started > timeout_seconds:
                timed_out = True
                process.kill()
            try:
                stream_name, line = line_queue.get(timeout=0.2)
            except queue.Empty:
                now = time.monotonic()
                if process.poll() is None and on_event and heartbeat_event_type and now - last_heartbeat >= 5:
                    on_event(
                        heartbeat_event_type,
                        "Codex is still running.",
                        {
                            "elapsed_seconds": int(now - started),
                            "seconds_since_last_output": int(now - last_output),
                        },
                    )
                    last_heartbeat = now
                continue
            if line is None:
                continue
            last_output = time.monotonic()
            if stream_name == "stdout":
                parsed = _parse_json_line(line)
                log.write(json.dumps(parsed or {"raw": line}, ensure_ascii=False) + "\n")
                log.flush()
                if on_event:
                    if parsed and default_event_type == "codex_json_event":
                        on_event("codex_json_event", _summarize_codex_event(parsed), {"raw": parsed})
                    else:
                        on_event(default_event_type if default_event_type != "codex_json_event" else "codex_stdout", line, {"line": line})
            else:
                log.write(json.dumps({"stderr": line}, ensure_ascii=False) + "\n")
                log.flush()
                if on_event:
                    on_event("codex_stderr", line, {"line": line, "severity": _classify_stderr(line)})

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()
    if process.returncode is None:
        process.wait(timeout=5)
    if timed_out:
        return CodexRunResult(exit_code=process.returncode, status="timeout", error_message="Process timed out.")
    if process.returncode == 0:
        return CodexRunResult(exit_code=0, status="completed")
    return CodexRunResult(exit_code=process.returncode, status="failed", error_message=f"Process exited with {process.returncode}.")


def _remote_join(*parts: str) -> str:
    cleaned: list[str] = []
    for index, part in enumerate(parts):
        value = str(part).replace("\\", "/").strip("/")
        if index == 0 and str(part).startswith("~/"):
            value = "~/" + str(part)[2:].strip("/")
        elif index == 0 and str(part) == "~":
            value = "~"
        cleaned.append(value)
    result = "/".join(part for part in cleaned if part)
    return result or "."


def _remote_shell_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized == "~":
        return "$HOME"
    if normalized.startswith("~/"):
        suffix = normalized[2:].strip("/")
        if not suffix:
            return "$HOME"
        return "$HOME/" + "/".join(shlex.quote(part) for part in suffix.split("/"))
    return shlex.quote(normalized)


def _remote_executable_path_prefix(executable: str) -> str:
    executable_dir = posixpath.dirname(executable.replace("\\", "/"))
    if not executable_dir:
        return ""
    prefixes = [executable_dir]
    if posixpath.basename(executable_dir) == "bin":
        prefixes.append(posixpath.join(posixpath.dirname(executable_dir), "sbin"))
    return ":".join(shlex.quote(item) for item in prefixes)


def _wrap_remote_command(job: CodexRunRequest, command: str) -> str:
    prefix = job.ssh_command_prefix.strip()
    if not prefix:
        return command
    return f"{prefix} {shlex.quote(command)}"


def _remote_no_tilde(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized == "~":
        return "."
    if normalized.startswith("~/"):
        return normalized[2:]
    return normalized

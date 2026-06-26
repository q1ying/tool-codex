from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import hashlib

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT / "apps" / "worker" / "src"))

from fastapi.testclient import TestClient

from gateway.config import get_settings, split_ssh_target
from gateway.db import init_db
from gateway.dependencies import services
from gateway.main import create_app
from gateway.services.path_security import safe_join
from codex_gateway_worker.codex_runner import CodexRunner, CodexRunRequest, SshCodexRunner, _classify_stderr


class MilestoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="gateway-test-"))
        os.environ["GATEWAY_DATA_DIR"] = str(self.tmpdir / "data")
        os.environ["DEFAULT_USER_ID"] = "user_default"
        os.environ["DEFAULT_CONVERSATION_ID"] = "1"
        os.environ["CODEX_EXECUTABLE"] = "python"
        os.environ["CODEX_RUNNER_MODE"] = "local"
        os.environ["CODEX_SSH_AUTH_METHOD"] = "key"
        os.environ["CODEX_SSH_PASSWORD"] = ""
        get_settings.cache_clear()
        init_db(get_settings().database_path)
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        get_settings.cache_clear()

    def test_create_conversation_creates_workspace(self) -> None:
        response = self.client.post(
            "/api/conversations",
            json={
                "title": "整理原始数据",
                "user_request": "请整理成专著附件格式",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        workspace = self.tmpdir / "data" / "workspaces" / "user_default" / data["conversation_id"]
        self.assertTrue((workspace / "inputs").is_dir())
        self.assertTrue((workspace / "outputs").is_dir())
        self.assertEqual(data["status"], "created")

    def test_upload_file_saved_to_inputs(self) -> None:
        conversation_id = self._create_conversation()
        response = self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("raw_data.csv", b"a,b\n1,2\n", "text/csv")},
            data={"kind": "input", "description": "原始数据"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()
        saved = self.tmpdir / "data" / "workspaces" / "user_default" / conversation_id / asset["relative_path"]
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_text(encoding="utf-8"), "a,b\n1,2\n")
        stored = services()["assets"].resolve_local_path(asset["file_id"])
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.read_text(encoding="utf-8"), "a,b\n1,2\n")

        assets = self.client.get(f"/api/conversations/{conversation_id}/assets")
        self.assertEqual(assets.status_code, 200, assets.text)
        self.assertEqual(assets.json()["items"][0]["asset_id"], asset["file_id"])
        link = services()["conn"].execute(
            "SELECT * FROM asset_links WHERE asset_id = ?",
            (asset["file_id"],),
        ).fetchone()
        self.assertEqual(link["relation_type"], "chat_attachment")

    def test_start_run_compiles_task_and_prompt_even_if_codex_missing(self) -> None:
        conversation_id = self._create_conversation()
        self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("raw_data.csv", b"a,b\n1,2\n", "text/csv")},
            data={"kind": "input"},
        )
        response = self.client.post(
            f"/api/conversations/{conversation_id}/runs",
            json={"user_instruction": "请整理字段名"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        workspace = self.tmpdir / "data" / "workspaces" / "user_default" / conversation_id
        prompt = (workspace / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("请整理字段名", prompt)

    def test_path_traversal_is_rejected(self) -> None:
        root = self.tmpdir / "workspace"
        root.mkdir()
        with self.assertRaises(ValueError):
            safe_join(root, "../outside.txt")
        with self.assertRaises(ValueError):
            safe_join(root, Path(self.tmpdir.anchor) / "absolute.txt")

    def test_codex_runner_builds_required_sandbox_command(self) -> None:
        workspace = self.tmpdir / "workspace"
        request = CodexRunRequest(
            run_id="run_0001",
            conversation_id="conv_0001",
            workspace_path=workspace,
            prompt_text="hello",
            final_message_path=workspace / "final.md",
            jsonl_log_path=workspace / "codex.jsonl",
            executable="codex",
            timeout_seconds=1,
        )
        cmd = CodexRunner().build_command(request)
        self.assertEqual(cmd[:4], ["codex", "exec", "-C", str(workspace)])
        self.assertIn("--sandbox", cmd)
        self.assertIn("workspace-write", cmd)
        self.assertNotIn("--ask-for-approval", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertIn("--json", cmd)
        self.assertIn("--output-last-message", cmd)
        self.assertEqual(cmd[-1], "-")
        self.assertNotIn("hello", cmd)

    def test_ssh_runner_builds_remote_codex_command(self) -> None:
        workspace = self.tmpdir / "workspace"
        request = CodexRunRequest(
            run_id="run_0001",
            conversation_id="conv_0001",
            workspace_path=workspace,
            prompt_text="hello",
            final_message_path=workspace / "final.md",
            jsonl_log_path=workspace / "codex.jsonl",
            runner_mode="ssh",
            ssh_host="codex.example.com",
            ssh_user="lqy",
            ssh_remote_root="~/lqy",
            ssh_executable="codex",
        )
        cmd = SshCodexRunner()._remote_codex_command(request, "~/lqy/conv_0001", "~/lqy/conv_0001/.gateway/run_final.md")
        self.assertIn("codex exec", cmd)
        self.assertIn("-C $HOME/lqy/conv_0001", cmd)
        self.assertIn("--sandbox workspace-write", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertIn("--output-last-message $HOME/lqy/conv_0001/.gateway/run_final.md", cmd)
        self.assertTrue(cmd.endswith(" -"))
        self.assertNotIn("hello", cmd)

    def test_stderr_classification_keeps_stdin_notice_as_info(self) -> None:
        self.assertEqual(_classify_stderr("Reading additional input from stdin..."), "info")
        self.assertEqual(_classify_stderr("zsh:1: command not found: codex"), "error")

    def test_submit_and_run_reuses_conversation_and_tracks_attachments(self) -> None:
        first = self.client.post(
            "/api/conversations/run",
            data={
                "title": "整理附件",
                "user_instruction": "第一次处理",
                "kind": "input",
                "description": "原始数据",
            },
            files=[("files", ("raw_data.csv", b"a,b\n1,2\n", "text/csv"))],
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_data = first.json()
        conversation_id = first_data["conversation"]["conversation_id"]
        self.assertEqual(conversation_id, "1")
        attachments = first_data["conversation"]["messages"][-1]["attachments"]
        self.assertEqual(attachments, [first_data["uploaded_files"][0]["file_id"]])
        self.assertTrue((self.tmpdir / "data" / "workspaces" / "user_default" / "1").is_dir())

        second = self.client.post(
            "/api/conversations/run",
            data={
                "title": "不会创建新会话",
                "user_instruction": "第二次处理",
                "kind": "input",
                "description": "补充数据",
            },
            files=[("files", ("more.csv", b"c,d\n3,4\n", "text/csv"))],
        )
        self.assertEqual(second.status_code, 200, second.text)
        second_data = second.json()
        self.assertEqual(second_data["conversation"]["conversation_id"], conversation_id)
        self.assertEqual(second_data["conversation"]["messages"][-1]["content"], "第二次处理")
        self.assertEqual(second_data["conversation"]["messages"][-1]["attachments"], [second_data["uploaded_files"][0]["file_id"]])
        runs = self.client.get(f"/api/conversations/{conversation_id}/runs").json()["items"]
        self.assertTrue(all("/1/runs/" in run["final_message_path"].replace("\\", "/") for run in runs))

    def test_duplicate_upload_reuses_existing_file_by_hash(self) -> None:
        conversation_id = self._create_conversation()
        payload = b"a,b\n1,2\n"
        first = self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("raw_data.csv", payload, "text/csv")},
            data={"kind": "input", "description": "first"},
        )
        second = self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("raw_data.csv", payload, "text/csv")},
            data={"kind": "input", "description": "second"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["file_id"], second.json()["file_id"])
        self.assertTrue(second.json()["metadata"]["duplicate_upload_skipped"])

        files = self.client.get(f"/api/conversations/{conversation_id}/files").json()["items"]
        self.assertEqual(len(files), 1)
        branches = self.client.get(f"/api/conversations/{conversation_id}/file-branches").json()["items"]
        self.assertEqual(branches[0]["stored_count"], 1)

        download = self.client.get(f"/api/conversations/files/{first.json()['file_id']}/download")
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.content, payload)

    def test_cors_preflight_allows_local_web_origin(self) -> None:
        response = self.client.options(
            "/api/conversations/run",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")

    def test_cors_header_is_added_to_error_responses(self) -> None:
        response = self.client.post(
            "/api/conversations/run",
            data={},
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")

    def test_ssh_check_is_skipped_in_local_mode(self) -> None:
        response = self.client.get("/api/system/ssh-check")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "skipped")

    def test_default_device_is_seeded_from_env(self) -> None:
        response = self.client.get("/api/devices")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["device_id"], "device_default")
        self.assertEqual(items[0]["runner_mode"], "local")
        self.assertEqual(items[0]["local_executable"], "python")

    def test_create_device_hides_ssh_password(self) -> None:
        response = self.client.post(
            "/api/devices",
            json={
                "name": "SSH worker",
                "runner_mode": "ssh",
                "host": "codex.example.com",
                "user": "openclaw",
                "ssh_auth_method": "password",
                "ssh_password": "secret",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertNotIn("ssh_password", data)
        self.assertTrue(data["has_ssh_password"])

    def test_device_health_check_updates_status(self) -> None:
        response = self.client.post("/api/devices/device_default/health-check")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok")

        device = self.client.get("/api/devices/device_default")
        self.assertEqual(device.status_code, 200, device.text)
        self.assertEqual(device.json()["last_check_status"], "ok")

    def test_run_records_selected_device_snapshot(self) -> None:
        conversation_id = self._create_conversation()
        response = self.client.post(
            f"/api/conversations/{conversation_id}/runs",
            json={"user_instruction": "run with default device"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        runs = self.client.get(f"/api/conversations/{conversation_id}/runs").json()["items"]
        self.assertEqual(runs[-1]["command"]["device"]["device_id"], "device_default")
        self.assertEqual(runs[-1]["command"]["runner_mode"], "local")

    def test_run_uses_session_workspace_and_manifest(self) -> None:
        conversation_id = self._create_conversation()
        upload = self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("raw_data.csv", b"a,b\n1,2\n", "text/csv")},
            data={"kind": "input"},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        response = self.client.post(
            f"/api/conversations/{conversation_id}/runs",
            json={"user_instruction": "run in a session workspace"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        run_id = response.json()["run_id"]

        session_response = self.client.get(f"/api/conversations/runs/{run_id}/session")
        self.assertEqual(session_response.status_code, 200, session_response.text)
        session = session_response.json()
        session_root = Path(session["root_path"])
        self.assertIn("sessions", session_root.parts)
        self.assertTrue((session_root / "task.json").is_file())
        self.assertTrue((session_root / "prompt.md").is_file())
        self.assertTrue((session_root / upload.json()["relative_path"]).is_file())
        self.assertTrue((session_root / ".gateway" / "manifest.json").is_file())
        self.assertEqual(session["manifest"]["files"][0]["asset_id"], upload.json()["file_id"])
        self.assertEqual(session["manifest"]["distribution"]["strategy_version"], "v1")
        self.assertEqual(session["manifest"]["files"][0]["mode"], "original")
        self.assertIn("strategy", session["manifest"]["files"][0])

        runs = self.client.get(f"/api/conversations/{conversation_id}/runs").json()["items"]
        self.assertIn("sessions", Path(runs[-1]["command"]["session"]["root_path"]).parts)
        self.assertEqual(runs[-1]["session"]["session_id"], session["session_id"])

    def test_distribution_prefers_ready_text_derivative_for_large_document(self) -> None:
        conversation_id = self._create_conversation()
        payload = b"x" * (2 * 1024 * 1024 + 1)
        upload = self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("large.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"kind": "input"},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        derivative_source = self.tmpdir / "large.extracted.md"
        derivative_source.write_text("# extracted\ncontent", encoding="utf-8")
        derivative_bytes = derivative_source.read_bytes()
        services()["assets"].create_derivative_from_path(
            asset_id=upload.json()["file_id"],
            derivative_type="extracted_text",
            source_path=derivative_source,
            stored_filename="large.extracted.md",
            mime_type="text/markdown",
            size_bytes=len(derivative_bytes),
            sha256=hashlib.sha256(derivative_bytes).hexdigest(),
        )

        response = self.client.post(
            f"/api/conversations/{conversation_id}/runs",
            json={"user_instruction": "use extracted text if available"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        session = self.client.get(f"/api/conversations/runs/{response.json()['run_id']}/session").json()
        item = session["manifest"]["files"][0]
        self.assertEqual(item["mode"], "extracted_text")
        self.assertEqual(item["strategy"], "large_document_text_preferred")
        self.assertTrue(item["derivative_id"])
        self.assertTrue(item["target_path"].startswith("inputs/"))
        self.assertTrue(item["target_path"].endswith("_large.extracted.md"))
        self.assertTrue((Path(session["root_path"]) / item["target_path"]).is_file())

    def test_split_ssh_target_accepts_user_at_host(self) -> None:
        host, user = split_ssh_target("openclaw@192.168.110.38", "")
        self.assertEqual(host, "192.168.110.38")
        self.assertEqual(user, "openclaw")
        host2, user2 = split_ssh_target("192.168.110.38", "openclaw")
        self.assertEqual(host2, "192.168.110.38")
        self.assertEqual(user2, "openclaw")

    def test_event_ids_are_unique_after_many_inserts(self) -> None:
        conversation_id = self._create_conversation()
        for _ in range(5):
            response = self.client.post(
                f"/api/conversations/{conversation_id}/files",
                files={"file": ("unique.csv", os.urandom(8), "text/csv")},
                data={"kind": "input"},
            )
            self.assertEqual(response.status_code, 200, response.text)
        events = self.client.get(f"/api/conversations/{conversation_id}/events").json()["items"]
        event_ids = [event["event_id"] for event in events]
        self.assertEqual(len(event_ids), len(set(event_ids)))

    def test_heartbeat_updates_runtime_without_persisting_event(self) -> None:
        conversation_id = self._create_conversation()
        svc = services()
        queued = svc["runs"].queue_run(conversation_id, "run with heartbeat")
        run_id = queued["run_id"]
        before = len(self.client.get(f"/api/conversations/{conversation_id}/events").json()["items"])
        svc["runs"]._handle_runner_event(
            conversation_id,
            run_id,
            "remote_codex_running",
            "Remote Codex is still running.",
            {"elapsed_seconds": 5, "seconds_since_last_output": 2},
        )
        after = len(self.client.get(f"/api/conversations/{conversation_id}/events").json()["items"])
        self.assertEqual(before, after)
        runtime = self.client.get(f"/api/conversations/runs/{run_id}/runtime")
        self.assertEqual(runtime.status_code, 200, runtime.text)
        self.assertTrue(runtime.json()["alive"])
        self.assertEqual(runtime.json()["elapsed_seconds"], 5)

    def test_register_run_outputs_creates_output_branch_and_download(self) -> None:
        conversation_id = self._create_conversation()
        upload = self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("source.csv", b"a,b\n1,2\n", "text/csv")},
            data={"kind": "input"},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        source_file_id = upload.json()["file_id"]
        workspace = self.tmpdir / "data" / "workspaces" / "user_default" / conversation_id
        output = workspace / "outputs" / "cleaned.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"a,b\n2,4\n")

        registered = services()["files"].register_run_outputs(
            conversation_id=conversation_id,
            user_id="user_default",
            run_id="run_test",
            source_file_ids=[source_file_id],
        )
        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0]["kind"], "output")
        self.assertTrue((workspace / registered[0]["relative_path"]).is_file())

        branches = self.client.get(f"/api/conversations/{conversation_id}/file-branches").json()["items"]
        group = next(item for item in branches if item["branch_key"] == "source.csv")
        self.assertEqual(group["stored_count"], 2)
        self.assertTrue(group["branches"][-1]["is_generated_output"])
        self.assertEqual(group["branches"][-1]["generated_from_file_ids"], [source_file_id])

        download = self.client.get(f"/api/conversations/files/{registered[0]['file_id']}/download")
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.content, b"a,b\n2,4\n")
        output_asset = self.client.get(f"/api/assets/{registered[0]['file_id']}")
        self.assertEqual(output_asset.status_code, 200, output_asset.text)
        self.assertEqual(output_asset.json()["scope_type"], "run")
        output_link = services()["conn"].execute(
            "SELECT * FROM asset_links WHERE asset_id = ?",
            (registered[0]["file_id"],),
        ).fetchone()
        self.assertEqual(output_link["relation_type"], "generated_output")

    def _create_conversation(self) -> str:
        response = self.client.post(
            "/api/conversations",
            json={
                "title": "整理原始数据",
                "user_request": "请整理成专著附件格式",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["conversation_id"]


if __name__ == "__main__":
    unittest.main()

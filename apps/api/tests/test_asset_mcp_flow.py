from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT / "apps" / "worker" / "src"))

from fastapi.testclient import TestClient

from gateway.config import get_settings
from gateway.db import init_db
from gateway.dependencies import services
from gateway.main import create_app


class AssetMcpFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="gateway-mcp-test-"))
        self.objects: dict[str, bytes] = {}
        os.environ["GATEWAY_DATA_DIR"] = str(self.tmpdir / "data")
        os.environ["DEFAULT_USER_ID"] = "user_default"
        os.environ["DEFAULT_CONVERSATION_ID"] = "1"
        os.environ["CODEX_MAX_RUNTIME_SECONDS"] = "1"
        os.environ["CODEX_DISABLE_EXTERNAL_MCPS"] = "false"
        os.environ["ASSET_MCP_URL"] = "http://127.0.0.1:8010/mcp"
        get_settings.cache_clear()
        init_db(get_settings().database_path)
        self.patches = [
            patch("gateway.services.storage_service.StorageService.put_path", self._put_path),
            patch("gateway.services.storage_service.StorageService.copy_to_path", self._copy_to_path),
            patch("gateway.services.storage_service.StorageService.object_size", self._object_size),
            patch("gateway.services.storage_service.StorageService.iter_bytes", self._iter_bytes),
            patch("gateway.services.storage_service.StorageService.delete_object", self._delete_object),
            patch("gateway.services.storage_service.StorageService.presign_download", self._presign_download),
            patch("gateway.services.storage_service.StorageService.presign_upload", self._presign_upload),
            patch("gateway.services.storage_service.StorageService.exists", self._exists),
        ]
        for item in self.patches:
            item.start()
        self.client_context = TestClient(create_app())
        self.client = self.client_context.__enter__()
        device = self.client.post(
            "/api/devices",
            json={
                "device_id": "device_test",
                "name": "Local test device",
                "runner_mode": "local",
                "local_executable": sys.executable,
                "status": "enabled",
                "disable_external_mcps": False,
            },
        )
        self.assertEqual(device.status_code, 200, device.text)

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        get_settings.cache_clear()

    def test_run_asset_mcp_and_direct_artifact_complete_flow(self) -> None:
        conversation_id = self._create_conversation()
        upload = self.client.post(
            f"/api/conversations/{conversation_id}/files",
            files={"file": ("report.txt", b"alpha beta report\nsecond line\n", "text/plain")},
            data={"kind": "material", "description": "实习报告原始文本"},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        asset_id = upload.json()["file_id"]

        run = self.client.post(
            f"/api/conversations/{conversation_id}/runs",
            json={"user_instruction": "请根据实习报告生成结果"},
        )
        self.assertEqual(run.status_code, 200, run.text)
        run_id = run.json()["run_id"]

        candidates = self.client.get(f"/api/runs/{run_id}/asset-candidates")
        self.assertEqual(candidates.status_code, 200, candidates.text)
        self.assertEqual(candidates.json()["items"][0]["asset_id"], asset_id)
        self.assertIn("实习报告", candidates.json()["items"][0]["summary"])

        token = self._run_asset_token(run_id)
        tools = self._mcp_call(token, "tools/list")
        tool_names = {item["name"] for item in tools["result"]["tools"]}
        self.assertIn("list_candidate_assets", tool_names)
        self.assertIn("search_assets", tool_names)
        self.assertIn("read_asset_chunk", tool_names)
        self.assertIn("complete_artifact", tool_names)

        mcp_candidates = self._mcp_tool(token, "list_candidate_assets")
        self.assertTrue(token.startswith("asset_mcp_"), token)
        self.assertEqual(mcp_candidates["items"][0]["asset_id"], asset_id)

        search = self.client.post(f"/api/runs/{run_id}/assets/search", json={"query": "实习 报告", "limit": 5})
        self.assertEqual(search.status_code, 200, search.text)
        self.assertEqual(search.json()["items"][0]["asset_id"], asset_id)

        chunk = self.client.get(f"/api/runs/{run_id}/assets/{asset_id}/chunks/0", params={"chunk_size": 10})
        self.assertEqual(chunk.status_code, 200, chunk.text)
        self.assertEqual(chunk.json()["text"], "alpha beta")

        download = self._mcp_tool(token, "get_asset_download_url", {"asset_id": asset_id})
        self.assertEqual(download["method"], "GET")
        self.assertIn(asset_id, download["url"])

        upload_url = self._mcp_tool(
            token,
            "get_artifact_upload_url",
            {"filename": "result.md", "content_type": "text/markdown", "role": "final"},
        )
        result_bytes = b"# result\nok\n"
        self.objects[upload_url["object_key"]] = result_bytes
        complete = self.client.post(
            f"/api/runs/{run_id}/artifacts/complete",
            json={
                "object_key": upload_url["object_key"],
                "filename": "result.md",
                "size_bytes": len(result_bytes),
                "sha256": hashlib.sha256(result_bytes).hexdigest(),
                "content_type": "text/markdown",
                "role": "final",
                "parent_asset_ids": [asset_id],
            },
        )
        self.assertEqual(complete.status_code, 200, complete.text)
        output_asset = complete.json()["asset"]
        self.assertEqual(output_asset["scope_type"], "run")
        self.assertEqual(output_asset["run_id"], run_id)
        self.assertEqual(output_asset["kind"], "output")
        self.assertEqual(output_asset["status"], "candidate")

        output_row = services()["conn"].execute(
            "SELECT * FROM file_assets WHERE file_id = ?",
            (output_asset["asset_id"],),
        ).fetchone()
        self.assertIsNotNone(output_row)
        lineage = services()["conn"].execute(
            "SELECT * FROM asset_lineage WHERE parent_asset_id = ? AND child_asset_id = ?",
            (asset_id, output_asset["asset_id"]),
        ).fetchone()
        self.assertIsNotNone(lineage)

    def test_complete_artifact_rejects_wrong_object_scope(self) -> None:
        conversation_id = self._create_conversation()
        run = self.client.post(
            f"/api/conversations/{conversation_id}/runs",
            json={"user_instruction": "创建输出"},
        )
        self.assertEqual(run.status_code, 200, run.text)
        response = self.client.post(
            f"/api/runs/{run.json()['run_id']}/artifacts/complete",
            json={
                "object_key": "users/other/conversations/1/runs/bad/pending-artifacts/result.md",
                "filename": "result.md",
            },
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_mcp_http_handshake_edges_do_not_return_empty_untyped_responses(self) -> None:
        conversation_id = self._create_conversation()
        run = self.client.post(
            f"/api/conversations/{conversation_id}/runs",
            json={"user_instruction": "MCP handshake"},
        )
        self.assertEqual(run.status_code, 200, run.text)
        token = self._run_asset_token(run.json()["run_id"])
        headers = {"Authorization": f"Bearer {token}", "Host": "127.0.0.1:8010"}

        slash = self.client.get("/mcp/", headers=headers, follow_redirects=False)
        self.assertIn(slash.status_code, {307, 405}, slash.text)

        options = self.client.options("/mcp", headers=headers)
        self.assertEqual(options.status_code, 204, options.text)
        self.assertIn("application/json", options.headers.get("content-type", ""))

        initialize = self.client.post(
            "/mcp",
            headers={
                **headers,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.1.0"},
                },
            },
        )
        self.assertEqual(initialize.status_code, 200, initialize.text)
        self.assertIn("application/json", initialize.headers.get("content-type", ""))
        self.assertEqual(initialize.json()["result"]["serverInfo"]["name"], "codex-gateway-assets")
        self.assertEqual(initialize.json()["result"]["protocolVersion"], "2025-06-18")

        initialized = self.client.post(
            "/mcp",
            headers={**headers, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        self.assertEqual(initialized.status_code, 202, initialized.text)
        self.assertIn("application/json", initialized.headers.get("content-type", ""))

        unsupported_version = self.client.post(
            "/mcp",
            headers={
                **headers,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2099-01-01",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        )
        self.assertEqual(unsupported_version.status_code, 400, unsupported_version.text)
        self.assertIn("application/json", unsupported_version.headers.get("content-type", ""))

        batch = self.client.post(
            "/mcp",
            headers={**headers, "Accept": "application/json, text/event-stream"},
            json=[{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}],
        )
        self.assertEqual(batch.status_code, 400, batch.text)
        self.assertIn("application/json", batch.headers.get("content-type", ""))

        delete = self.client.delete("/mcp", headers=headers)
        self.assertEqual(delete.status_code, 405, delete.text)
        self.assertIn("application/json", delete.headers.get("content-type", ""))

    def _create_conversation(self) -> str:
        response = self.client.post(
            "/api/conversations",
            json={"title": "MCP 测试", "user_request": "请处理上传文件"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["conversation_id"]

    def _run_asset_token(self, run_id: str) -> str:
        row = services()["conn"].execute("SELECT metadata_json FROM codex_runs WHERE run_id = ?", (run_id,)).fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata_json"] or "{}")
        return metadata["asset_mcp"]["token"]

    def _mcp_call(self, token: str, method: str, params: dict | None = None) -> dict:
        response = self.client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Host": "127.0.0.1:8010",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _mcp_tool(self, token: str, name: str, arguments: dict | None = None) -> dict:
        payload = self._mcp_call(
            token,
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        self.assertNotIn("error", payload, payload)
        text = payload["result"]["content"][0]["text"]
        return json.loads(text)

    def _put_path(self, source_path: Path, object_key: str, content_type: str | None = None) -> None:
        self.objects[object_key] = Path(source_path).read_bytes()

    def _copy_to_path(self, object_key: str, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(self.objects[object_key])

    def _object_size(self, object_key: str) -> int:
        return len(self.objects[object_key])

    def _iter_bytes(self, object_key: str, chunk_size: int = 1024 * 1024):
        data = self.objects[object_key]
        for index in range(0, len(data), chunk_size):
            yield data[index : index + chunk_size]

    def _delete_object(self, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def _exists(self, object_key: str) -> bool:
        return object_key in self.objects

    def _presign_download(self, object_key: str) -> str:
        return f"https://minio.test/download/{object_key}"

    def _presign_upload(self, object_key: str, content_type: str | None = None) -> str:
        return f"https://minio.test/upload/{object_key}"


if __name__ == "__main__":
    unittest.main()

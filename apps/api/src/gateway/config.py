from functools import lru_cache
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def split_ssh_target(host: str, user: str) -> tuple[str, str]:
    if "@" in host and not user:
        parsed_user, parsed_host = host.split("@", 1)
        return parsed_host, parsed_user
    return host, user


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        load_dotenv(PROJECT_ROOT / ".env")
        cwd_dotenv = Path(".env").resolve()
        if cwd_dotenv != (PROJECT_ROOT / ".env").resolve():
            load_dotenv(cwd_dotenv)
        self.default_user_id = os.getenv("DEFAULT_USER_ID", "user_default")
        self.default_conversation_id = os.getenv("DEFAULT_CONVERSATION_ID", "1")
        self.data_dir = Path(os.getenv("GATEWAY_DATA_DIR", "data")).resolve()
        self.database_path = self.data_dir / "gateway.sqlite3"
        self.codex_max_runtime_seconds = int(os.getenv("CODEX_MAX_RUNTIME_SECONDS", "900"))
        self.codex_sandbox_mode = os.getenv("CODEX_SANDBOX_MODE", "workspace-write")
        self.codex_disable_external_mcps = env_bool("CODEX_DISABLE_EXTERNAL_MCPS", True)
        self.asset_mcp_url = os.getenv("ASSET_MCP_URL", "http://127.0.0.1:8010/mcp")
        self.max_upload_size_mb = int(os.getenv("MAX_UPLOAD_SIZE_MB", "200"))
        self.object_storage_endpoint = os.getenv("OBJECT_STORAGE_ENDPOINT", "http://127.0.0.1:19000")
        self.object_storage_bucket = os.getenv("OBJECT_STORAGE_BUCKET", "mathpilot-dev")
        self.object_storage_access_key_id = os.getenv("OBJECT_STORAGE_ACCESS_KEY_ID", "minioadmin")
        self.object_storage_secret_access_key = os.getenv("OBJECT_STORAGE_SECRET_ACCESS_KEY", "minioadmin123")
        self.object_storage_region = os.getenv("OBJECT_STORAGE_REGION", "us-east-1")
        self.object_storage_addressing_style = os.getenv("OBJECT_STORAGE_ADDRESSING_STYLE", "path")
        self.object_storage_signature_version = os.getenv("OBJECT_STORAGE_SIGNATURE_VERSION", "s3v4")
        self.object_storage_presign_expires_seconds = int(os.getenv("OBJECT_STORAGE_PRESIGN_EXPIRES_SECONDS", "1800"))
        self.object_storage_auto_create_bucket = os.getenv("OBJECT_STORAGE_AUTO_CREATE_BUCKET", "true").lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

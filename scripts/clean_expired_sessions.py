from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from gateway.config import get_settings  # noqa: E402
from gateway.db import connect, init_db  # noqa: E402
from gateway.services.asset_service import AssetService  # noqa: E402
from gateway.services.distribution_service import DistributionService  # noqa: E402
from gateway.services.session_workspace_service import SessionWorkspaceService  # noqa: E402
from gateway.services.storage_service import StorageService  # noqa: E402


def main() -> None:
    settings = get_settings()
    init_db(settings.database_path)
    with connect(settings.database_path) as conn:
        storage = StorageService(settings)
        assets = AssetService(conn, storage)
        distribution = DistributionService(conn, assets)
        sessions = SessionWorkspaceService(conn, settings.data_dir, distribution)
        cleaned = sessions.cleanup_expired()
    print(f"cleaned {len(cleaned)} expired session(s)")


if __name__ == "__main__":
    main()

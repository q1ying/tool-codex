from __future__ import annotations

import json


def main() -> None:
    result = {
        "status": "skipped",
        "message": "Device checks are stored in SQLite. Use the web Devices panel or POST /api/devices/{device_id}/health-check.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

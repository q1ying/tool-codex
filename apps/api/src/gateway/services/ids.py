from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{stamp}_{uuid4().hex[:8]}"


def short_id(prefix: str) -> str:
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{stamp}_{uuid4().hex[:8]}"

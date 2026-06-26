from __future__ import annotations

from dataclasses import dataclass, asdict
import threading
from typing import Any

from .ids import now_iso


@dataclass
class RunRuntimeSnapshot:
    run_id: str
    alive: bool
    status: str
    last_heartbeat_at: str | None = None
    elapsed_seconds: int = 0
    seconds_since_last_output: int | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunRuntimeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, RunRuntimeSnapshot] = {}

    def start(self, run_id: str) -> None:
        now = now_iso()
        with self._lock:
            self._items[run_id] = RunRuntimeSnapshot(
                run_id=run_id,
                alive=True,
                status="running",
                last_heartbeat_at=now,
                updated_at=now,
            )

    def heartbeat(self, run_id: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        now = now_iso()
        with self._lock:
            item = self._items.get(run_id)
            if item is None:
                item = RunRuntimeSnapshot(run_id=run_id, alive=True, status="running")
                self._items[run_id] = item
            item.alive = True
            item.status = "running"
            item.last_heartbeat_at = now
            item.updated_at = now
            item.elapsed_seconds = int(payload.get("elapsed_seconds") or item.elapsed_seconds or 0)
            seconds_since = payload.get("seconds_since_last_output")
            if seconds_since is not None:
                item.seconds_since_last_output = int(seconds_since)

    def finish(self, run_id: str, status: str) -> None:
        now = now_iso()
        with self._lock:
            item = self._items.get(run_id)
            if item is None:
                item = RunRuntimeSnapshot(run_id=run_id, alive=False, status=status)
                self._items[run_id] = item
            item.alive = False
            item.status = status
            item.updated_at = now

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(run_id)
            return item.to_dict() if item else None


runtime_state = RunRuntimeState()

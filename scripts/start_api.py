from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    if "PATH" in env and "Path" in env:
        env.pop("PATH")
    env.setdefault("GATEWAY_DATA_DIR", str(root / "data"))
    out = (root / "uvicorn.out.log").open("ab")
    err = (root / "uvicorn.err.log").open("ab")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "gateway.main:app",
            "--app-dir",
            "apps/api/src",
            "--host",
            "127.0.0.1",
            "--port",
            "8010",
        ],
        cwd=root,
        env=env,
        stdout=out,
        stderr=err,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    (root / "uvicorn.pid").write_text(str(process.pid), encoding="ascii")
    print(process.pid)


if __name__ == "__main__":
    main()


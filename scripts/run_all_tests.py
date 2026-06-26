from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    failed = False
    if _has_vitest():
        failed = _run(["npx", "vitest", "run"], "Vitest") or failed
    else:
        print("== Vitest ==")
        print("未发现 package.json/vitest.config.*，跳过前端 Vitest。")

    print("== Pytest ==")
    if _module_available("pytest"):
        failed = _run([sys.executable, "-m", "pytest", "apps/api/tests"], "Pytest") or failed
    else:
        print("未安装 pytest，回退到 unittest。")
        failed = _run([sys.executable, "-m", "unittest", "discover", "-s", "apps/api/tests", "-p", "test_*.py"], "unittest") or failed
    return 1 if failed else 0


def _has_vitest() -> bool:
    if any(ROOT.glob("vitest.config.*")):
        return True
    for package_json in ROOT.rglob("package.json"):
        if any(part in {"node_modules", ".git", "data"} for part in package_json.parts):
            continue
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        deps = {
            **(data.get("dependencies") or {}),
            **(data.get("devDependencies") or {}),
        }
        if "vitest" in deps or "test:vitest" in (data.get("scripts") or {}):
            return shutil.which("npx") is not None
    return False


def _module_available(name: str) -> bool:
    return subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _run(cmd: list[str], label: str) -> bool:
    print(f"== {label} ==")
    completed = subprocess.run(cmd, cwd=ROOT)
    return completed.returncode != 0


if __name__ == "__main__":
    raise SystemExit(main())

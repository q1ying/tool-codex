from pathlib import Path


def ensure_within(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {candidate}") from exc
    return candidate_resolved


def safe_join(root: Path, relative_path: str | Path) -> Path:
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe relative path: {relative_path}")
    return ensure_within(root, root / rel)


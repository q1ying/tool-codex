from pathlib import Path
import shutil


def main() -> None:
    data_dir = Path("data")
    for child in ("workspaces", "conversations"):
        target = data_dir / child
        if target.exists():
            shutil.rmtree(target)
    print("cleaned data/workspaces and data/conversations")


if __name__ == "__main__":
    main()

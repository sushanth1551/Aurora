"""Create the standard AURORA local data directories idempotently."""

from pathlib import Path


def main() -> None:
    """Ensure data directories exist relative to the project root."""
    root = Path(__file__).resolve().parents[1]
    for name in ("raw", "processed", "reports", "content"):
        (root / "data" / name).mkdir(parents=True, exist_ok=True)
    print(f"Data directories are ready under {root / 'data'}.")


if __name__ == "__main__":
    main()

"""Check local runtime executables needed for AURORA development."""

from __future__ import annotations

import shutil
import sys


def main() -> int:
    """Report required local tools and return a non-zero status if missing."""
    required = ("python",)
    optional = ("node", "npm", "docker")
    missing = [command for command in required if shutil.which(command) is None]
    print(f"Python: {sys.version.split()[0]}")
    for command in optional:
        print(f"{command}: {'available' if shutil.which(command) else 'not found'}")
    if sys.version_info[:2] != (3, 12):
        print("Python 3.12 is required.")
        return 1
    if missing:
        print(f"Missing development tools: {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

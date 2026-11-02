"""Reject staged paths under data/ and CSV files over 500 KB outside results/."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

MAX_CSV_BYTES = 500_000


def problems(paths: list[str]) -> list[str]:
    """Return one message per rejected path."""
    found: list[str] = []
    for name in paths:
        path = PurePosixPath(name.replace("\\", "/"))
        if path.parts and path.parts[0] == "data":
            found.append(f"{name}: nothing under data/ may be committed")
            continue
        if path.suffix.lower() != ".csv" or (path.parts and path.parts[0] == "results"):
            continue
        local = Path(name)
        if local.is_file() and local.stat().st_size > MAX_CSV_BYTES:
            found.append(f"{name}: CSV over 500 KB outside results/")
    return found


def main(argv: list[str]) -> int:
    """Print each problem and return a non-zero status if there is any."""
    found = problems(argv)
    for message in found:
        print(message, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

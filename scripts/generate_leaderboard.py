#!/usr/bin/env python3
"""Compatibility entrypoint — prefer scripts/reproduce.sh.

Runs the SpatialBench-aligned aggregation pipeline end-to-end.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    script = REPO / "scripts" / "reproduce.sh"
    raise SystemExit(subprocess.call(["bash", str(script)], cwd=REPO))


if __name__ == "__main__":
    main()

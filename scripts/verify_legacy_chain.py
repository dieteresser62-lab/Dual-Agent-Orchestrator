#!/usr/bin/env python3
"""CLI for the archived, read-only structured-v2 legacy verifier."""

from __future__ import annotations

from pathlib import Path
import sys


sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from legacy_verifier import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

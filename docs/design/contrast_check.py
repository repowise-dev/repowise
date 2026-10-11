#!/usr/bin/env python3
"""Wrapper — the canonical contrast gate ships with the ui package.

The gate lives at packages/ui/scripts/contrast-check.py so downstream
consumers run the same check from node_modules. This wrapper keeps the
historical invocation (`python3 docs/design/contrast_check.py`) working.
"""
from __future__ import annotations

import pathlib
import runpy
import sys

GATE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "packages" / "ui" / "scripts" / "contrast-check.py"
)

if __name__ == "__main__":
    sys.argv[0] = str(GATE)
    runpy.run_path(str(GATE), run_name="__main__")

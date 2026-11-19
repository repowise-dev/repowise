"""Write tests/shard_weights.json from the JUnit reports of full unit runs.

``pytest --shard K/N`` (tests/conftest.py) packs the files listed there by
their recorded seconds, so the CI shards finish close together. Refresh it
when one shard runs much longer than the others, from several green runs so
one slow runner does not skew it:

    gh run download <run id> -p 'junit-*' -D junit/<run id>   # per run
    python scripts/shard_weights.py junit/*/*/junit.xml

Each file runs in one shard per run, so a file's weight is the median of its
seconds across the reports that hold it. The reports must carry each case's
``file`` attribute, which pytest writes with ``-o junit_family=xunit1`` (the
CI shards pass it). Files under one second, or no longer in the tree, are
left out; new files shard by a hash of their path.
"""

from __future__ import annotations

import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "shard_weights.json"
#: Files faster than this are not worth a line; a hash spreads them evenly.
FLOOR_SECONDS = 1.0


def file_seconds(reports: list[str]) -> dict[str, float]:
    """Seconds per test file: its cases summed per report, median over reports."""
    runs: dict[str, list[float]] = {}
    for report in reports:
        seconds: dict[str, float] = {}
        for case in ET.parse(report).iter("testcase"):
            path = case.get("file")  # absent on a collection error
            if path:
                path = path.replace("\\", "/")
                seconds[path] = seconds.get(path, 0.0) + float(case.get("time") or 0)
        for path, s in seconds.items():
            runs.setdefault(path, []).append(s)
    if not runs:
        raise SystemExit("No case has a file attribute; run pytest with -o junit_family=xunit1.")
    return {path: statistics.median(values) for path, values in runs.items()}


def main(reports: list[str]) -> int:
    if not reports:
        raise SystemExit(__doc__)
    weights = {
        path: round(s, 1)
        for path, s in sorted(file_seconds(reports).items())
        if s >= FLOOR_SECONDS and (ROOT / path).is_file()
    }
    if not weights:
        raise SystemExit("No reported file is in this tree; run from the checkout the reports ran.")
    OUT.write_text(json.dumps(weights, indent=0) + "\n", encoding="utf-8")
    print(f"{len(weights)} files, {sum(weights.values()):.0f} s, written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

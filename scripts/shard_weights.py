"""Write tests/shard_weights.json from the JUnit reports of a full unit run.

``pytest --shard K/N`` (tests/conftest.py) packs the files listed there by
their recorded seconds, so the CI shards finish close together. Refresh it
when a shard runs much longer than the other:

    gh run download <run id> -p 'junit-*' -D junit
    python scripts/shard_weights.py junit/*/junit.xml

The reports must carry each case's ``file`` attribute, which pytest writes
with ``-o junit_family=xunit1`` (the CI shards pass it). Files under one
second are left out; they shard by a hash of their path.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "shard_weights.json"
#: Files faster than this are not worth a line; a hash spreads them evenly.
FLOOR_SECONDS = 1.0


def file_seconds(reports: list[str]) -> dict[str, float]:
    """Seconds per test file, summed over every case in *reports*."""
    seconds: dict[str, float] = {}
    for report in reports:
        for case in ET.parse(report).iter("testcase"):
            path = case.get("file")  # absent on a collection error
            if path:
                path = path.replace("\\", "/")
                seconds[path] = seconds.get(path, 0.0) + float(case.get("time") or 0)
    if not seconds:
        raise SystemExit("No case has a file attribute; run pytest with -o junit_family=xunit1.")
    return seconds


def main(reports: list[str]) -> int:
    if not reports:
        raise SystemExit(__doc__)
    weights = {
        path: round(s, 1) for path, s in sorted(file_seconds(reports).items()) if s >= FLOOR_SECONDS
    }
    OUT.write_text(json.dumps(weights, indent=0) + "\n", encoding="utf-8")
    print(f"{len(weights)} files, {sum(weights.values()):.0f} s, written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

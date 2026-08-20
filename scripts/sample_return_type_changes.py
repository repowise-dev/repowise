"""Draw a deterministic audit sample from a return-type edge comparison."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("measurement", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260821)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    payload = json.loads(args.measurement.read_text(encoding="utf-8"))
    rng = random.Random(args.seed)
    samples = {}
    for bucket in ("added", "retargeted", "refused", "lost"):
        rows = payload.get("rows", {}).get(bucket, [])
        samples[bucket] = rows if len(rows) <= args.limit else rng.sample(rows, args.limit)
    result = {
        "measurement": str(args.measurement),
        "seed": args.seed,
        "limit": args.limit,
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

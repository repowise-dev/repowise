"""Loader for the checked-in performance opportunity corpus.

The corpus lives in ``tests/fixtures/perf_corpus`` so a characterization run
needs no repository download, no index, and no network. See that directory's
README for the runner and the regeneration switch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CORPUS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "perf_corpus"
REWRITE_ENV = "REPOWISE_REWRITE_PERF_GOLDEN"


def rewrite_requested() -> bool:
    return os.environ.get(REWRITE_ENV, "") not in {"", "0", "false", "False"}


def _read(name: str) -> Any:
    return json.loads((CORPUS_DIR / name).read_text(encoding="utf-8"))


def observation_rows() -> list[dict[str, Any]]:
    """Expand the compact corpus records into raw performance finding rows.

    Rows are plain dicts on purpose: ``build_performance_opportunities`` accepts
    analyzer dataclasses, ORM rows, and dicts through one attribute adapter, and
    the dict form is the one that keeps the corpus readable in review.
    """
    rows: list[dict[str, Any]] = []
    for record in _read("observations.json")["observations"]:
        rows.append(
            {
                "id": record["finding_ref"],
                "dimension": "performance",
                "biomarker_type": record["biomarker_type"],
                "file_path": record["file_path"],
                "function_name": record["function_name"],
                "line_start": record["line_start"],
                "line_end": record["line_end"],
                "reason": record["reason"],
                "details": {
                    "boundary_kind": record["boundary_kind"],
                    "cross_function": bool(record["path"]),
                    "path": list(record["path"]),
                    "resolution_basis": record["resolution_basis"],
                    "reliable_entry_reachability": record["reliable_entry_reachability"],
                    **record["extra"],
                },
            }
        )
    return rows


def rows_for(case: str) -> list[dict[str, Any]]:
    records = _read("observations.json")["observations"]
    keep = {record["finding_ref"] for record in records if record["case"] == case}
    return [row for row in observation_rows() if row["id"] in keep]


def load_golden(name: str) -> Any:
    return _read(name)


def write_golden(name: str, payload: Any) -> None:
    with (CORPUS_DIR / name).open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

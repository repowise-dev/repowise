"""The four 0.62 kinds read, join and store byte-identically through the kind registry.

``fixtures/v1/golden.json`` was written by this module (run it as a script)
before ``evidence.kinds`` existed. A change here is a change to what a stored
run holds, so regenerate it only on purpose.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.ingest import (
    EvidenceCaps,
    IndexedSymbol,
    RunProvenance,
    cap_rows,
    entry_paths,
    join_entries,
    place_test_entries,
    run_fields,
    run_key,
    symbol_lookup,
)

FIXTURES = Path(__file__).parent / "fixtures" / "v1"
GOLDEN = FIXTURES / "golden.json"
KINDS = ("profile", "benchmark", "test_timing", "query_count")

_BY_FILE = {
    "src/app.py": symbol_lookup(
        [
            IndexedSymbol("src/app.py::build", "build", "build", 10),
            IndexedSymbol("src/app.py::Reader.read", "Reader.read", "read", 30),
            IndexedSymbol("src/app.py::Writer.read", "Writer.read", "read", 50),
            IndexedSymbol("src/app.py::parse#1", "parse", "parse", 70),
            IndexedSymbol("src/app.py::parse#2", "parse", "parse", 80),
        ]
    ),
    "gen/pb.py": symbol_lookup([IndexedSymbol("gen/pb.py::decode", "decode", "decode", 1)]),
    "src/edited.py": symbol_lookup([IndexedSymbol("src/edited.py::f", "f", "f", 1)]),
    "tests/test_app.py": symbol_lookup(
        [
            IndexedSymbol("tests/test_app.py::test_build", "test_build", "test_build", 4),
            IndexedSymbol("tests/test_app.py::TestX::test_y", "TestX.test_y", "test_y", 9),
        ]
    ),
    "tests/unit/test_x.py": symbol_lookup(
        [IndexedSymbol("tests/unit/test_x.py::TestC::test_z", "TestC.test_z", "test_z", 3)]
    ),
}
_ORIGINS = {"gen/pb.py": "generated"}
_CHANGED = frozenset({"src/edited.py"})
_CAPS = EvidenceCaps(max_symbols_per_run=3, max_rows_per_run=3, history_runs=5, max_runs=20)
_PROVENANCE = RunProvenance(
    workload_name="golden",
    workload_class="entry_point",
    commit_sha="a" * 40,
    checkout_root="/repo",
    dirty=False,
)


def snapshot(kind: str) -> dict:
    # The run key digests the bytes: line endings a checkout chose must not move it.
    text = (FIXTURES / f"{kind}.json").read_text(encoding="utf-8").replace("\r\n", "\n")
    doc = place_test_entries(parse_evidence(text), list(_BY_FILE))
    joined = join_entries(doc, _BY_FILE, _ORIGINS, _CHANGED)
    rows, truncated = cap_rows(doc.kind, [dict(r) for r in joined.rows], _CAPS)
    return {
        "document": dataclasses.asdict(doc),
        "entry_paths": sorted(entry_paths(doc)),
        "joined": {
            "rows": joined.rows,
            "matched": joined.matched,
            "skipped": dict(sorted(joined.skipped.items())),
            "unlinked": dict(sorted(joined.unlinked.items())),
            "unmatched_sample": joined.unmatched_sample,
        },
        "capped": {"rows": rows, "truncated": truncated},
        "run": run_fields(
            doc, _PROVENANCE, joined, key="k", truncated=truncated, mapping_partial=False
        ),
        "run_key": run_key(text, _PROVENANCE, doc.kind, "b" * 40),
    }


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=1)


#: Document fields added after the golden was written, unset by these files.
_LATER_FIELDS = ("window_start", "window_end", "base_commit_sha")


@pytest.mark.parametrize("kind", KINDS)
def test_a_v1_kind_reads_joins_and_stores_as_before_the_registry(kind: str) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))[kind]
    now = snapshot(kind)
    # A field the document gained reads as absent; nothing else may move.
    for name in _LATER_FIELDS:
        assert now["document"].pop(name) is None
        golden["document"].pop(name, None)
    assert _dump(now) == _dump(golden)


if __name__ == "__main__":  # pragma: no cover - regenerates the golden on purpose
    GOLDEN.write_text(_dump({kind: snapshot(kind) for kind in KINDS}) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {GOLDEN}\n")

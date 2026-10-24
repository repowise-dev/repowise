"""Time budget: no dialect costs what an ungated scan of every file costs.

Every dialect runs over every file of its extensions, so one that skips its
gate, or gates on a token every file holds while scanning with an
identifier-led regex, makes extraction grow with each dialect added. Measured
on an 8,141-file corpus, the five heaviest dialects before their gates cost
650 to 1,200 microseconds per file; gated, every dialect costs under a quarter
of the reference below.

Wall time is machine-dependent, so the budget is relative: each dialect is
timed against one identifier-led regex pass over the same text in the same
process (the shape of an ungated dialect), and may cost at most 40% of it.
The text is this repository's own source, given each dialect's extension, so
a Java or Go dialect is timed on code-shaped text too.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from repowise.core.workspace.extractors import data, graphql, grpc, http, socket, topic
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.dialect import run_dialect

_ROOT = Path(__file__).resolve().parents[3] / "packages"
_SAMPLES = (("core/src/repowise/core", "*.py", ".py"), ("ui/src", "*.tsx", ".tsx"))
_PER_SAMPLE = 40
_ROUNDS = 5
#: A dialect may cost at most this share of the reference scan.
_BUDGET = 0.4

# An ungated dialect's shape: tried at every identifier of every file.
_REFERENCE_RE = re.compile(r"(\w+)\s*\.\s*(\w+)\s*\(")

#: Contract file formats are read whole by a parser: every such file is one.
_FORMATS = frozenset({".proto", ".sql", ".prisma", ".graphql", ".graphqls", ".gql"})

SOURCE_DIALECTS = tuple(
    d
    for d in (
        *http.PROVIDER_DIALECTS,
        *http.CONSUMER_DIALECTS,
        *topic.DIALECTS,
        *socket.DIALECTS,
        *grpc.DIALECTS,
        *graphql.DIALECTS,
        *data.PROVIDER_DIALECTS,
        *data.CONSUMER_DIALECTS,
    )
    if not d.extensions <= _FORMATS
)


def _texts() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for folder, pattern, kind in _SAMPLES:
        paths = sorted((_ROOT / folder).rglob(pattern))[:_PER_SAMPLE]
        out[kind] = [p.read_text(encoding="utf-8", errors="replace") for p in paths]
    return out


def _timed(run) -> float:
    start = time.perf_counter()
    run()
    return time.perf_counter() - start


@pytest.fixture(scope="module")
def texts() -> dict[str, list[str]]:
    found = _texts()
    if not all(found.values()):
        pytest.skip("the repository's own sources are not in this checkout")
    return found


def _kinds(dialect) -> list[tuple[str, str]]:
    """``(text kind, suffix)`` pairs to time *dialect* on: Python text for a
    Python extension, C-shaped text for every other one it reads."""
    exts = sorted(dialect.extensions)
    py = [e for e in exts if e in (".py", ".pyi")]
    other = [e for e in exts if e not in (".py", ".pyi")]
    return [(".py", e) for e in py[:1]] + [(".tsx", e) for e in other[:1]]


@pytest.mark.parametrize("dialect", SOURCE_DIALECTS, ids=lambda d: f"{type(d).__name__}:{d.name}")
def test_a_dialect_costs_less_than_an_ungated_scan(dialect, texts) -> None:
    for kind, suffix in _kinds(dialect):
        files = texts[kind]

        def reference(files=files) -> None:
            for text in files:
                for _ in _REFERENCE_RE.finditer(text):
                    pass

        def run(files=files, suffix=suffix) -> None:
            # A fresh context per round: the per-file gate memo must not carry over.
            for i, text in enumerate(files):
                run_dialect(dialect, ScanContext("svc", f"src/f{i}{suffix}", suffix, text))

        # Interleaved, so a stall on a busy worker hits both sides of one round,
        # and the best round of each is compared.
        spent = ref = float("inf")
        for _ in range(_ROUNDS):
            ref = min(ref, _timed(reference))
            spent = min(spent, _timed(run))
        assert spent <= _BUDGET * ref, (
            f"{dialect.name} took {spent * 1e3:.1f} ms on {suffix} where an ungated scan of the "
            f"same {len(files)} files took {ref * 1e3:.1f} ms: check its `requires` gate"
        )

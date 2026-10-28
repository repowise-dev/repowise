"""The dead-code source scans and passes give the same answers as their slower forms.

The path-token regex skips match starts inside a word, the occurrence scans
read each distinct token once, and the file passes skip symbol nodes. Each is
checked here against the form it replaced.
"""

from __future__ import annotations

import random
import re

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.name_occurrences import (
    _PATH_TOKEN_RE,
    IDENTIFIER_RE,
    _targets_in,
    _targets_named_by,
    occurrence_files,
)
from repowise.core.analysis.dead_code.passes.unreachable_files import UnreachableFilePass
from tests.unit.dead_code._helpers import _build_graph, _old_date

_REFERENCE_PATH_TOKEN_RE = re.compile(rb"[A-Za-z0-9_@~+\-]*[./][A-Za-z0-9_.@~+\-/]*")

_SAMPLES = [
    b"",
    b"plain words only with no separators at all",
    b"import pkg.mod.name; from ./rel/path.ts import x",
    b'"@scope/pkg/index.js" ~/home/file.sh a-b.c+d ..//.. ./. /abs',
    b"averyveryverylongidentifierwithoutanydotfollowedby.ext and/slash",
    b"tab\tsep.a\nnew/line.b\r\nx.y.z/w",
    "unicode café/naïve.py ü.b".encode(),
    b"trailing.dot. trailing/slash/ .leading /leading",
]


def _fuzz_blobs(count: int = 300) -> list[bytes]:
    rng = random.Random(1234)
    alphabet = b"ab_Z09-+@~./ \n\"'(){}:\x00\xc3\xa9"
    return [bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 200))) for _ in range(count)]


def test_path_token_regex_matches_the_reference_regex():
    for blob in _SAMPLES + _fuzz_blobs():
        assert _PATH_TOKEN_RE.findall(blob) == _REFERENCE_PATH_TOKEN_RE.findall(blob)


def test_targets_in_matches_a_per_match_scan():
    wanted = {"src/a.py": {"src/a.py"}, "src/a": {"src/a.py"}, "b.json": {"b.json"}}
    tails = {"a.py", "a", "b.json"}
    blob = b"run src/a.py then ./b.json and src/a.py again; src/a b.json"
    reference = {
        t
        for m in _REFERENCE_PATH_TOKEN_RE.finditer(blob)
        for t in _targets_named_by(m.group().decode("ascii"), wanted, tails, "")
    }
    assert _targets_in(blob, wanted, tails, "") == reference == {"src/a.py", "b.json"}


def test_occurrence_files_matches_a_per_match_scan():
    blobs = _SAMPLES + _fuzz_blobs()
    source = {f"f{i}.py": blob for i, blob in enumerate(blobs)}
    names = {b"words", b"pkg", b"abZ", b"leading", b"missing"}
    reference: dict[bytes, set[str]] = {}
    for path, blob in source.items():
        for m in IDENTIFIER_RE.finditer(blob):
            if m.group() in names:
                reference.setdefault(m.group(), set()).add(path)
    found = occurrence_files(source, names)
    assert found == reference
    assert list(found) == list(reference)


def test_unreachable_file_pass_judges_files_not_symbols():
    # A symbol with no in-edge at all is still not a file with no importer.
    symbol = {"name": "f", "kind": "function", "visibility": "public", "language": "python"}
    g = _build_graph({"lonely.py": {"symbols": [symbol]}})
    g.remove_edge("lonely.py", "lonely.py::f")
    meta = {"lonely.py": {"commit_count_90d": 0, "last_commit_at": _old_date(400)}}
    analyzer = DeadCodeAnalyzer(g, git_meta_map=meta)
    assert [f.file_path for f in UnreachableFilePass(analyzer).run(set())] == ["lonely.py"]
    report = analyzer.analyze({"detect_unused_exports": False, "detect_unused_internals": False})
    unreachable = [f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE]
    assert unreachable == ["lonely.py"]

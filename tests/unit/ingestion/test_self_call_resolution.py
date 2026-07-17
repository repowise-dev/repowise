"""Behavior pins for CallResolver member-call strategies 3 and 4.

Strategy 3 (self/this receiver) used to scan ``_file_methods`` for every file
in the repo just to find the caller's own entry; the fix is a direct dict
lookup. Strategy 4 (unique global class, any-file method scan) was provably
shadowed: any (class, method) pair present in ANY file's method index is
already resolved by strategy 2 (same file, 0.93) or strategy 2b (global
method index, 0.75) before strategy 4 is reached. These tests pin the
observable resolution behavior around both so the rewrite is equivalence-
checked, not just plausible.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        fi = _file_info(rel, abs_, lang)
        out[rel] = parse_file(fi, content.encode("utf-8"))
    return out


def _edges(parsed, tmp_path, import_targets=None):
    resolver = CallResolver(parsed, import_targets or {}, repo_path=str(tmp_path))
    edges = []
    for path, pf in parsed.items():
        for rc in resolver.resolve_file(path, pf.calls):
            edges.append((rc.caller_id, rc.callee_id, rc.confidence))
    return edges


WIDGET_PY = '''
class Widget:
    def helper(self):
        return 1

    def run(self):
        return self.helper()


class Other:
    def lonely(self):
        return self.helper()
'''

PAINTER_PY = '''
class Painter:
    def draw(self):
        return "ok"
'''

CALLER_PY = '''
def use():
    return Painter.draw()
'''

MISSING_PY = '''
def use_missing():
    return Painter.missing()
'''


class TestSelfCallStrategy:
    def test_self_call_resolves_within_same_class(self, tmp_path: Path) -> None:
        parsed = _parse_all(tmp_path, {"src/widget.py": ("python", WIDGET_PY)})
        edges = _edges(parsed, tmp_path)
        hits = [
            e
            for e in edges
            if e[0].endswith("::Widget::run") and e[1].endswith("::Widget::helper")
        ]
        assert hits, f"self.helper() inside Widget.run must resolve; edges: {edges}"
        assert hits[0][2] == 0.95

    def test_self_call_does_not_cross_classes(self, tmp_path: Path) -> None:
        """Other.lonely calls self.helper() but Other has no helper: no edge."""
        parsed = _parse_all(tmp_path, {"src/widget.py": ("python", WIDGET_PY)})
        edges = _edges(parsed, tmp_path)
        bad = [e for e in edges if e[0].endswith("::Other::lonely")]
        assert bad == [], f"cross-class self-call must not resolve: {bad}"


class TestStrategy4Shadowing:
    def test_cross_file_class_method_resolves_via_global_index(self, tmp_path: Path) -> None:
        """Painter.draw() from a non-importing file resolves at 2b's 0.75,
        never at old strategy 4's 0.50."""
        parsed = _parse_all(
            tmp_path,
            {
                "src/painter.py": ("python", PAINTER_PY),
                "src/caller.py": ("python", CALLER_PY),
            },
        )
        edges = _edges(parsed, tmp_path)
        hits = [e for e in edges if e[1].endswith("::Painter::draw")]
        assert hits, f"Painter.draw() must resolve cross-file; edges: {edges}"
        assert hits[0][2] == 0.75

    def test_unknown_method_on_unique_class_stays_unresolved(self, tmp_path: Path) -> None:
        """Painter is a unique global class but has no `missing` method: the
        old strategy-4 any-file scan could never find one either, so the
        outcome (no edge) is identical with the scan removed."""
        parsed = _parse_all(
            tmp_path,
            {
                "src/painter.py": ("python", PAINTER_PY),
                "src/missing_caller.py": ("python", MISSING_PY),
            },
        )
        edges = _edges(parsed, tmp_path)
        bad = [e for e in edges if e[0].endswith("::use_missing")]
        assert bad == [], f"unknown method must stay unresolved: {bad}"

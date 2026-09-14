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

import pytest

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


# ---------------------------------------------------------------------------
# Self-dispatch across the languages whose self-reference keyword is NOT an
# ordinary identifier node.
#
# Python reaches strategy 3 because ``self`` parses as ``(identifier)`` and the
# stock member-call pattern captures it. In these six grammars the keyword is a
# distinct node type (``this`` / ``this_expression`` / ``self_expression``, and
# an anonymous ``"this"`` token in C#), so the member-call pattern used to match
# nothing at all — not a lower-confidence edge, none — and the whole call site
# was dropped before resolution. The receiver slot is now an alternation.
#
# Each case pins both directions: the in-class call resolves at strategy 3's
# 0.95, and the same call from a class WITHOUT that method stays unresolved, so
# widening the receiver did not turn into a name-only match.
# ---------------------------------------------------------------------------

TS_SRC = """
class Widget {
  helper(): number { return 1; }
  run(): number { return this.helper(); }
}

class Other {
  lonely(): number { return this.helper(); }
}
"""

JS_SRC = """
class Widget {
  helper() { return 1; }
  run() { return this.helper(); }
}

class Other {
  lonely() { return this.helper(); }
}
"""

CS_SRC = """
class Widget {
    int helper() { return 1; }
    int run() { return this.helper(); }
}

class Other {
    int lonely() { return this.helper(); }
}
"""

KT_SRC = """
class Widget {
    fun helper(): Int { return 1 }
    fun run(): Int { return this.helper() }
}

class Other {
    fun lonely(): Int { return this.helper() }
}
"""

SWIFT_SRC = """
class Widget {
    func helper() -> Int { return 1 }
    func run() -> Int { return self.helper() }
}

class Other {
    func lonely() -> Int { return self.helper() }
}
"""

# Same shape, but with JSX in the method bodies: a .tsx file binds the
# JSX-aware grammar, and plain TypeScript would exercise it only incidentally.
# The render method is what makes the file actually need that grammar.
TSX_SRC = """
class Widget {
  helper(): number { return 1; }
  run(): number { return this.helper(); }
  render() { return <div className="w">{this.run()}</div>; }
}

class Other {
  lonely(): number { return this.helper(); }
}
"""

# (id, relative path, language tag, source). ``.tsx`` is listed separately
# because it binds the JSX-aware grammar while reusing typescript.scm — a
# pattern valid against one grammar is not automatically valid against the other.
SELF_DISPATCH_CASES = [
    ("typescript", "src/widget.ts", "typescript", TS_SRC),
    ("tsx", "src/widget.tsx", "typescript", TSX_SRC),
    ("javascript", "src/widget.js", "javascript", JS_SRC),
    ("csharp", "src/Widget.cs", "csharp", CS_SRC),
    ("kotlin", "src/Widget.kt", "kotlin", KT_SRC),
    ("swift", "src/Widget.swift", "swift", SWIFT_SRC),
]


@pytest.mark.parametrize(
    ("rel", "lang", "source"),
    [c[1:] for c in SELF_DISPATCH_CASES],
    ids=[c[0] for c in SELF_DISPATCH_CASES],
)
class TestSelfDispatchAcrossLanguages:
    def test_self_call_is_recorded_as_a_call_site(
        self, tmp_path: Path, rel: str, lang: str, source: str
    ) -> None:
        """The parser must emit a CallSite at all — the original bug dropped it."""
        parsed = _parse_all(tmp_path, {rel: (lang, source)})
        receivers = {c.receiver_name for c in parsed[rel].calls if c.target_name == "helper"}
        assert receivers, f"no call site recorded for the self-call in {rel}"
        assert receivers <= {"this", "self"}, (
            f"self-call receiver must be this/self so strategy 3 fires; got {receivers}"
        )

    def test_self_call_resolves_within_same_class(
        self, tmp_path: Path, rel: str, lang: str, source: str
    ) -> None:
        parsed = _parse_all(tmp_path, {rel: (lang, source)})
        edges = _edges(parsed, tmp_path)
        hits = [
            e
            for e in edges
            if e[0].endswith("::Widget::run") and e[1].endswith("::Widget::helper")
        ]
        assert hits, f"this.helper() inside Widget.run must resolve; edges: {edges}"
        assert hits[0][2] == 0.95

    def test_self_call_does_not_cross_classes(
        self, tmp_path: Path, rel: str, lang: str, source: str
    ) -> None:
        """Other.lonely calls this.helper() but Other has no helper: no edge."""
        parsed = _parse_all(tmp_path, {rel: (lang, source)})
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


OBJC_INHERITED_SELECTOR = '''
@implementation ImageRep
- (void)load {
    [self setProperty:NSImageLoopCount withValue:@(2)];
    [self decode];
}
- (void)decode {}
@end
'''


def test_objc_self_send_of_an_inherited_selector_resolves_to_nothing(tmp_path: Path):
    """A framework method called on ``self`` has no in-repo target.

    ``-setProperty:withValue:`` belongs to the AppKit superclass, so the repo
    holds no declaration of it anywhere. The self-receiver tier is a lookup in
    the ``(class, selector)`` index rather than an id built from the two
    halves, so it declines; a synthesized id here would mint a confident edge
    to a symbol that does not exist. The sibling call in the same body pins
    that the tier still answers when the method is real.
    """
    pytest.importorskip("tree_sitter_objc")
    parsed = _parse_all(tmp_path, {"ImageRep.m": ("objectivec", OBJC_INHERITED_SELECTOR)})
    edges = _edges(parsed, tmp_path)
    callees = {callee for _caller, callee, _conf in edges}
    assert not any("setProperty" in c for c in callees)
    assert "ImageRep.m::ImageRep::decode" in callees

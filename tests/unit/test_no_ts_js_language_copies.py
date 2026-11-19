"""The TS/JS language family is spelled once, as ``models.TS_JS_LANGUAGES``.

Hand-copied lists drifted: Astro joined some and was missed in others. A
literal holding four or more of the family's tags and nothing else is a copy,
complete or not. A set that adds other languages (one with ``tsx``) passes, and
a deliberate subset is listed in ``_KNOWN``.
"""

from __future__ import annotations

import ast
import pathlib

from repowise.core.ingestion.models import TS_JS_LANGUAGES

# Remix's route languages leave out Vue on purpose.
_KNOWN: frozenset[str] = frozenset(
    {"packages/core/src/repowise/core/ingestion/framework_edges/remix.py"}
)

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"
_HOME = "packages/core/src/repowise/core/ingestion/models.py"


def _copies(path: pathlib.Path) -> list[int]:
    text = path.read_text(encoding="utf-8")
    # A copy spells at least four tags, so a file with fewer needs no parse.
    if sum(tag in text for tag in TS_JS_LANGUAGES) < 4:
        return []
    tree = ast.parse(text)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Tuple | ast.Set | ast.List)
        and all(isinstance(e, ast.Constant) for e in node.elts)
        and len(vals := {e.value for e in node.elts}) >= 4
        and vals <= TS_JS_LANGUAGES
    ]


def test_no_new_ts_js_language_copies() -> None:
    offenders = {
        rel: lines
        for path in _PACKAGES.rglob("*.py")
        if (rel := path.relative_to(_PACKAGES.parent).as_posix()) not in {_HOME, *_KNOWN}
        and (lines := _copies(path))
    }
    assert not offenders, f"Import TS_JS_LANGUAGES from ingestion/models.py instead: {offenders}"

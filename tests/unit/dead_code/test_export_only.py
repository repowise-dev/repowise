"""An unused export its own file uses is export-only, not dead code.

``export const SITE_URL`` read by the function below it has no importer, but
deleting it breaks that function: only the ``export`` keyword is unneeded. Such
a row stays an ``unused_export`` below the default floor, marked
``export_only``, never deletion-ready and worth no deletable line. A mention
in a comment, a docstring or a Python ``__all__`` entry is not a use; a
template literal's ``${NAME}`` is.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.models import DeadCodeFindingData
from repowise.core.analysis.dead_code.name_occurrences import (
    demote_exports_used_in_own_code,
    demote_used_in_own_file,
)
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
from tests.unit.dead_code._helpers import _build_graph


def _exports_over(path: str, source: str, symbols: list[dict], language: str) -> dict:
    """Unused-export findings for one file (with one importer) analysed with its source."""
    g = _build_graph(
        nodes={
            path: {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbols": [
                    {"visibility": "public", "decorators": [], "language": language, **s}
                    for s in symbols
                ],
            },
            "src/main.ts": {"is_entry_point": True, "is_test": False},
        },
        # An importer naming another symbol, so import absence is evidence (1.0).
        edges=[("src/main.ts", path, {"edge_type": "imports", "imported_names": ["other"]})],
    )
    report = DeadCodeAnalyzer(g, git_meta_map={}, source_map={path: source.encode()}).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    return {f.symbol_name: f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}


def _const(name: str, line: int) -> dict:
    return {"name": name, "kind": "variable", "start_line": line, "end_line": line}


def test_ts_export_read_in_its_own_file_is_export_only_and_never_deletable():
    source = (
        'export const SITE_URL = "https://x.dev";\n'  # 1
        "export const UNUSED_URL = 1;\n"  # 2
        "export function other(m: string) {\n"  # 3
        "  return `${SITE_URL}/a#${m}`;\n"  # 4
        "}\n"
    )
    found = _exports_over(
        "src/links.ts",
        source,
        [_const("SITE_URL", 1), _const("UNUSED_URL", 2), {**_const("other", 3), "end_line": 5}],
        "typescript",
    )
    used = found["SITE_URL"]
    assert used.export_only is True
    assert used.safe_to_delete is False
    assert used.confidence < RISK_CAP_CONFIDENCE
    assert "used in its own file" in used.reason
    assert "src/links.ts:4" in used.evidence[-1]
    # The control: a truly unused export is untouched.
    dead = found["UNUSED_URL"]
    assert dead.export_only is False
    assert dead.confidence >= RISK_CAP_CONFIDENCE


def test_export_only_rows_add_no_deletable_lines():
    source = "export const LIMIT = 5;\nexport function other() { return LIMIT; }\n"
    g_findings = _exports_over(
        "src/a.ts",
        source,
        [_const("LIMIT", 1), {**_const("other", 2), "kind": "function"}],
        "typescript",
    )
    assert g_findings["LIMIT"].export_only
    assert not any(f.safe_to_delete for f in g_findings.values() if f.export_only)


def test_ts_mention_in_a_comment_or_string_is_not_a_use():
    source = (
        "export const LIMIT = 5;\n"  # 1
        "// LIMIT is documented here\n"  # 2
        'export const label = "LIMIT";\n'  # 3
    )
    found = _exports_over(
        "src/a.ts", source, [_const("LIMIT", 1), _const("label", 3)], "typescript"
    )
    assert found["LIMIT"].export_only is False


def test_python_name_listed_only_in_all_is_not_a_use():
    source = (
        "def plan_json(plan):\n"  # 1
        '    """plan_json renders the plan."""\n'  # 2
        "    return plan\n"  # 3
        "\n"
        "def fmt(x): return x\n"  # 5
        "def other():\n"  # 6
        "    return sorted([], key=fmt)\n"  # 7
        "\n"
        '__all__ = ["plan_json", "fmt", "other"]\n'  # 9
    )
    found = _exports_over(
        "pkg/mod.py",
        source,
        [
            {"name": "plan_json", "kind": "function", "start_line": 1, "end_line": 3},
            {"name": "fmt", "kind": "function", "start_line": 5, "end_line": 5},
            {"name": "other", "kind": "function", "start_line": 6, "end_line": 7},
        ],
        "python",
    )
    assert found["plan_json"].export_only is False
    # A use the graph missed (a callable passed as a value) is still a use.
    assert found["fmt"].export_only is True
    assert found["fmt"].safe_to_delete is False


@pytest.mark.parametrize(
    "clause",
    [
        "export { LIMIT };",
        "export { LIMIT as MAX };",
        "export {\n  LIMIT,\n};",
        "export default LIMIT;",
    ],
)
def test_an_export_clause_naming_it_is_not_a_use(clause: str):
    source = f"const LIMIT = 5;\n{clause}\n"
    found = _exports_over("src/a.ts", source, [_const("LIMIT", 1)], "typescript")
    assert found["LIMIT"].export_only is False
    assert found["LIMIT"].confidence >= RISK_CAP_CONFIDENCE


def test_a_function_that_only_calls_itself_stays_dead():
    source = (
        "export function walk(n: number): number {\n"  # 1
        "  return n > 0 ? walk(n - 1) : 0;\n"  # 2
        "}\n"  # 3
    )
    found = _exports_over(
        "src/walk.ts",
        source,
        [{"name": "walk", "kind": "function", "start_line": 1, "end_line": 3}],
        "typescript",
    )
    assert found["walk"].export_only is False
    assert found["walk"].confidence >= RISK_CAP_CONFIDENCE


def test_a_row_marked_twice_carries_one_evidence_line():
    path = "src/a.ts"
    source = {path: b"export const LIMIT = 5;\nexport const two = LIMIT * 2;\n"}
    finding = DeadCodeFindingData(
        kind=DeadCodeKind.UNUSED_EXPORT,
        file_path=path,
        symbol_name="LIMIT",
        symbol_kind="variable",
        confidence=1.0,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=1,
        evidence=[],
        safe_to_delete=True,
        primary_owner=None,
        age_days=None,
        start_line=1,
        end_line=1,
    )
    demote_used_in_own_file([finding], source)
    demote_exports_used_in_own_code([finding], source)
    assert finding.export_only is True
    assert finding.safe_to_delete is False
    assert len(finding.evidence) == 1

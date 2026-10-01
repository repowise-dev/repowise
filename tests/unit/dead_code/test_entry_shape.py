"""An unreachable file shaped like something loaded by path is a review candidate.

A default-export-only module, a script, and a same-shape sibling set are all
loaded without an import edge. Each is capped to the review tier and kept in
the report; a plain dead library module keeps its confidence.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.entry_shape import clamp_entry_shaped
from repowise.core.analysis.dead_code.models import DeadCodeFindingData
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
from tests.unit.dead_code._helpers import _build_graph, _old_date


def _file(path: str, confidence: float = 1.0) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=DeadCodeKind.UNREACHABLE_FILE,
        file_path=path,
        symbol_name=None,
        symbol_kind=None,
        confidence=confidence,
        reason="File has no importers (in_degree=0)",
        last_commit_at=None,
        commit_count_90d=0,
        lines=1,
        evidence=[],
        safe_to_delete=True,
        primary_owner=None,
        age_days=None,
    )


def _clamp(
    files: dict[str, str], names: dict[str, set[str]] | None = None
) -> dict[str, DeadCodeFindingData]:
    findings = [_file(p) for p in files]
    public = {p: frozenset((names or {}).get(p, ())) for p in files}
    clamp_entry_shaped(findings, {p: s.encode() for p, s in files.items()}, public)
    return {f.file_path: f for f in findings}


def _capped(finding: DeadCodeFindingData) -> bool:
    return finding.confidence == RISK_CAP_CONFIDENCE and finding.safe_to_delete is False


# --- default export only -----------------------------------------------------


def test_a_convention_loaded_default_export_directory_is_capped():
    files = {
        "app/routes/home.tsx": "export default function Home() { return null }\n",
        "app/routes/about.tsx": "const About = () => null\nexport default About\n",
        "app/routes/config.ts": "export default { runtime: 'edge' }\n",
    }
    out = _clamp(files, {"app/routes/home.tsx": {"Home"}})
    for finding in out.values():
        assert _capped(finding)
        assert "only export is `default`" in finding.evidence[-1]


def test_a_default_export_beside_a_named_export_is_not_default_only():
    files = {"src/widget.ts": "export const helper = 1\nexport default function W() {}\n"}
    out = _clamp(files, {"src/widget.ts": {"helper", "W"}})
    assert out["src/widget.ts"].confidence == 1.0


def test_a_default_export_inside_a_block_comment_does_not_count():
    files = {"src/old.ts": "/*\nexport default Old\n*/\nconst x = 1\n"}
    assert _clamp(files)["src/old.ts"].confidence == 1.0


def test_a_lone_commonjs_module_exports_is_a_default_export():
    body = "function helper() {}\nmodule.exports = function handler() { helper() }\n"
    # CommonJS keeps every top-level name public, so the graph names both.
    out = _clamp({"api/hook.js": body}, {"api/hook.js": {"helper"}})
    assert _capped(out["api/hook.js"])


def test_commonjs_named_exports_are_not_default_only():
    files = {
        "lib/a.js": "module.exports = {\n  a: 1,\n}\n",
        "lib/b.js": "exports.b = 1\n",
        "lib/c.js": "module.exports = run\nmodule.exports.extra = 2\n",
    }
    out = _clamp(files, {p: {"x"} for p in ("lib/a.js",)})
    assert all(f.confidence == 1.0 for f in out.values())


def test_default_export_text_in_a_python_file_is_not_a_js_default():
    out = _clamp({"pkg/gen.py": 'TEMPLATE = """\nexport default {}\n"""\n'})
    assert out["pkg/gen.py"].confidence == 1.0


# --- scripts ---------------------------------------------------------------------


def test_a_main_guard_marks_a_script():
    body = "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
    out = _clamp({"tools/bump.py": body}, {"tools/bump.py": {"main"}})
    assert _capped(out["tools/bump.py"])
    assert "__main__" in out["tools/bump.py"].evidence[-1]


def test_top_level_statements_mark_a_script():
    body = '"""Seed the database."""\nimport db\n\ndb.connect()\nfor row in db.rows():\n    print(row)\n'
    out = _clamp({"scripts/seed.py": body})
    assert _capped(out["scripts/seed.py"])
    assert "line 4" in out["scripts/seed.py"].evidence[-1]


def test_a_shebang_marks_a_script():
    out = _clamp({"bin/release": "#!/usr/bin/env node\nconsole.log(1)\n"})
    assert _capped(out["bin/release"])


def test_a_plain_library_module_stays_a_positive():
    body = '"""Old helpers."""\nimport os\n\nLIMIT = 3\n\ndef helper():\n    return os.sep\n'
    out = _clamp({"pkg/old_helpers.py": body}, {"pkg/old_helpers.py": {"helper", "LIMIT"}})
    assert out["pkg/old_helpers.py"].confidence == 1.0
    assert out["pkg/old_helpers.py"].evidence == []


def test_only_whole_file_findings_are_capped():
    finding = _file("tools/bump.py")
    finding.kind = DeadCodeKind.UNUSED_EXPORT
    finding.symbol_name = "main"
    clamp_entry_shaped([finding], {"tools/bump.py": b"main()\n"}, {})
    assert finding.confidence == 1.0


def test_unparseable_python_is_left_alone():
    out = _clamp({"pkg/legacy.py": "print 'py2'\n"})
    assert out["pkg/legacy.py"].confidence == 1.0


# --- cohorts ---------------------------------------------------------------------


def _handler(name: str) -> str:
    return f"def handle(event):\n    return '{name}'\n"


def test_three_siblings_with_one_export_shape_are_a_cohort():
    paths = ["handlers/a.py", "handlers/b.py", "handlers/c.py"]
    out = _clamp({p: _handler(p) for p in paths}, {p: {"handle"} for p in paths})
    for p in paths:
        assert _capped(out[p])
        assert "One of 3 unreachable files" in out[p].evidence[-1]


def test_two_siblings_are_not_a_cohort():
    paths = ["handlers/a.py", "handlers/b.py"]
    out = _clamp({p: _handler(p) for p in paths}, {p: {"handle"} for p in paths})
    assert all(out[p].confidence == 1.0 for p in paths)


def test_a_cohort_needs_one_directory_and_one_shape():
    paths = ["a/x.py", "b/y.py", "c/z.py", "d/w.py"]
    names = {"a/x.py": {"handle"}, "b/y.py": {"handle"}, "c/z.py": {"handle"}, "d/w.py": {"run"}}
    out = _clamp({p: _handler(p) for p in paths}, names)
    assert all(f.confidence == 1.0 for f in out.values())


def test_files_with_no_exports_are_not_a_cohort():
    paths = ["pkg/a.py", "pkg/b.py", "pkg/c.py"]
    out = _clamp({p: "_x = 1\n" for p in paths})
    assert all(f.confidence == 1.0 for f in out.values())


def test_an_already_capped_sibling_still_counts_but_is_not_touched_again():
    paths = ["jobs/a.py", "jobs/b.py", "jobs/c.py"]
    findings = [_file(paths[0], confidence=0.3), _file(paths[1]), _file(paths[2])]
    clamp_entry_shaped(
        findings,
        {p: _handler(p).encode() for p in paths},
        {p: frozenset({"handle"}) for p in paths},
    )
    assert findings[0].confidence == 0.3 and findings[0].evidence == []
    assert all(_capped(f) for f in findings[1:])


# --- through the analyzer --------------------------------------------------------


def test_the_analyzer_applies_the_cap_and_keeps_the_finding():
    g = _build_graph(
        nodes={
            "pkg/main.py": {"is_entry_point": True, "symbols": []},
            "pkg/old.py": {"symbols": [{"name": "helper", "visibility": "public"}]},
            "pkg/run_once.py": {"symbols": [{"name": "main", "visibility": "public"}]},
        },
    )
    old = {"commit_count_90d": 0, "last_commit_at": _old_date(400)}
    source = {
        "pkg/main.py": b"print('hi')\n",
        "pkg/old.py": b"def helper():\n    return 1\n",
        "pkg/run_once.py": b"def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
    }
    report = DeadCodeAnalyzer(
        g, git_meta_map={"pkg/old.py": old, "pkg/run_once.py": old}, source_map=source
    ).analyze(
        {
            "detect_unused_exports": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
        }
    )
    by_path = {f.file_path: f for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}
    assert by_path["pkg/old.py"].confidence == 1.0
    assert by_path["pkg/run_once.py"].confidence == RISK_CAP_CONFIDENCE

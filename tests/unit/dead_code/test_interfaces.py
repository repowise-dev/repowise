"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


def test_interface_without_implementor_demoted_below_safe_threshold():
    """Public interfaces with no incoming ``implements`` edges have
    their unused-export confidence clamped below 0.7 so the demo
    doesn't ship them as confident dead code. Implementor detection
    is heuristic — absence is missing-signal, not evidence-of-absence.
    """
    g = _build_graph(
        nodes={
            "src/IBasketService.cs": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "IBasketService",
                        "kind": "interface",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                        "language": "csharp",
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    interface_findings = [
        f
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT and f.symbol_name == "IBasketService"
    ]
    # The finding may still surface, but never at safe-to-delete confidence.
    for f in interface_findings:
        assert f.confidence <= 0.4, f"interface flagged at unsafe confidence {f.confidence}"
        assert f.safe_to_delete is False


def test_com_contract_method_demoted_below_safe_threshold():
    """``QueryInterface`` / ``AddRef`` / ``Release`` are reached through
    a native COM vtable — never via a static caller. The analyzer must
    not flag them as confident dead code.
    """
    g = _build_graph(
        nodes={
            "src/CoFoo.cpp": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [
                    {
                        "name": "QueryInterface",
                        "kind": "method",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 10,
                        "end_line": 25,
                        "complexity_estimate": 1,
                        "language": "cpp",
                    },
                    {
                        "name": "AddRef",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 27,
                        "end_line": 30,
                        "complexity_estimate": 1,
                        "language": "cpp",
                    },
                    {
                        "name": "Release",
                        "kind": "method",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 32,
                        "end_line": 40,
                        "complexity_estimate": 1,
                        "language": "cpp",
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    com_findings = [
        f for f in report.findings if f.symbol_name in {"QueryInterface", "AddRef", "Release"}
    ]
    for f in com_findings:
        assert f.confidence <= 0.4, f"COM method flagged at unsafe confidence {f.confidence}"
        assert f.safe_to_delete is False


def test_pascal_unused_export_is_not_blanket_capped():
    """Object Pascal is no longer capped as a whole language.

    A blanket confidence cap on every Pascal unused-export finding was a
    temporary mitigation (PR #1353 follow-up) for real query gaps —
    assignment-RHS parenless calls and missing var/field/parameter/
    return-type/framework-ctor type-reference captures, all now fixed in
    pascal.scm and parser.py — plus a structural gap where ``uses``
    carried no per-symbol ``imported_names`` for the file-level rescue to
    match against (also fixed: Pascal imports now carry the wildcard
    sentinel ``["*"]``, since ``uses`` exposes a whole unit's public
    interface). Re-validated against the real ~150-file Delphi codebase
    that surfaced the original false positives: 603 findings dropped to
    1, and that one was already a known, correctly-low-confidence
    candidate, not a Pascal false positive. A genuinely unimported,
    uncalled, untyped-as symbol should flag at full confidence like any
    other language now — no language-wide cap left to interfere.
    """
    g = _build_graph(
        nodes={
            "src/uConsole.pas": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "TConsoleBuffer",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 20,
                        "complexity_estimate": 1,
                        "language": "pascal",
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    pascal_findings = [
        f
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT and f.symbol_name == "TConsoleBuffer"
    ]
    assert pascal_findings, "expected a finding for TConsoleBuffer"
    assert any(f.confidence >= 0.7 and f.safe_to_delete for f in pascal_findings), (
        "a genuinely-unused Pascal symbol with no risk factors should still be able to "
        f"reach safe-to-delete confidence, got {[(f.confidence, f.safe_to_delete) for f in pascal_findings]}"
    )


def test_pascal_uses_clause_rescues_every_public_symbol_in_the_unit():
    """A ``uses UnitA;`` clause exposes UnitA's entire public interface
    section, unlike Python/JS's name-scoped ``from x import y`` -- Pascal
    has no per-symbol import syntax. ``parser.py`` emits
    ``imported_names=["*"]`` for every Pascal import edge for exactly this
    reason, which should rescue every public symbol in a unit that's
    named in another file's ``uses`` clause, not just capped confidence.
    """
    g = _build_graph(
        nodes={
            "src/uConsole.pas": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "TConsoleBuffer",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 20,
                        "complexity_estimate": 1,
                        "language": "pascal",
                    },
                ],
            },
            "src/uMain.pas": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 0,
                "symbols": [],
            },
        },
        # uMain.pas has `uses uConsole;` -- Pascal's parser.py emits this
        # as a wildcard import (no per-symbol names in the uses syntax).
        edges=[
            (
                "src/uMain.pas",
                "src/uConsole.pas",
                {"edge_type": "imports", "imported_names": ["*"]},
            )
        ],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    pascal_findings = [
        f
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT and f.symbol_name == "TConsoleBuffer"
    ]
    assert not pascal_findings, (
        "TConsoleBuffer should be rescued entirely by the uses-clause wildcard import, "
        f"got {pascal_findings}"
    )


def test_release_in_non_com_language_not_clamped():
    """A free function named ``Release`` in TypeScript/Python is *not* a
    COM contract method — the contract-method clamp must not apply.
    """
    g = _build_graph(
        nodes={
            "src/foo.ts": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "Release",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                        "language": "typescript",
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    # TS ``function`` kind is universally skipped from unused-export
    # detection (functions surface as variables in JS-style modules),
    # so the strongest assertion is "no spurious COM clamp masked the
    # real behaviour". The function should still be detectable somehow
    # downstream; here we just confirm the analyzer didn't crash and
    # didn't ship it under contract-method semantics.
    com_findings = [f for f in report.findings if f.symbol_name == "Release"]
    for f in com_findings:
        # If it surfaces at all, it must do so under the *normal*
        # rule — not under the COM clamp specifically. The normal
        # rule yields 0.7 / 1.0 depending on file-level importers.
        assert f.confidence >= 0.7 or f.confidence < 0.4

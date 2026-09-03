"""Issue #1910 — dynamic-use uncertainty must cap unused-export confidence.

The unreachable-files pass already clamps a finding whose *package* uses
runtime-dispatch machinery (``importlib``, reflection, ``getattr`` keyed by a
configured string — see ``test_dynamic_import_dirs.py``). The unused-exports
pass had no equivalent: it only rescued a file a ``dynamic_uses``/``framework``
graph *edge* pinned to it (the resolved case). When the mechanism was present
but the target was not pinned — a registry value, a callback, a plugin looked
up by a name the graph never sees — the symbol shipped at high confidence with
``safe_to_delete=True``.

Every unresolved case carries a control: a genuinely unused symbol in a package
with no dynamic machinery must keep its original confidence, so a fix that
clamps everything fails the suite.

The scenario shapes come from the issue's sealed fixture:

- a class stored as a registry value (``registry['serializer'] = cls``);
- a function passed as a callback (``callbacks.append(after_commit)``);
- a reflective / string-dispatched reference
  (``getattr(module, configured_name)``) — the name never appears literally,
  so the name-occurrence clamp (``name_occurrences``) cannot see it;
- an exported symbol with no reference of any kind (the control);
- two same-named symbols where only one sits in a dynamically-loaded package.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
from tests.unit.dead_code._helpers import _build_graph, _old_date

_EXPORT_ONLY = {
    "detect_unreachable_files": False,
    "detect_unused_internals": False,
    "detect_zombie_packages": False,
    "min_confidence": 0.0,
}


def _parsed(rel: str) -> SimpleNamespace:
    """A ParsedFile stub whose ``file_info.abs_path`` exists (as the marker
    prepasses require) without needing real files on disk."""
    return SimpleNamespace(file_info=SimpleNamespace(abs_path=str(Path("/repo") / rel)))


def _export(report, symbol_name: str, *, file_path: str | None = None):
    hits = [
        f
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT
        and f.symbol_name == symbol_name
        and (file_path is None or f.file_path == file_path)
    ]
    assert len(hits) == 1, (
        f"expected exactly one unused-export finding for {symbol_name}, got {hits}"
    )
    return hits[0]


def _stale_meta(*paths: str) -> dict[str, dict]:
    """Git metadata that scores an unreferenced symbol at full confidence."""
    return {
        p: {"commit_count_90d": 0, "last_commit_at": _old_date(days=400), "age_days": 500}
        for p in paths
    }


def _analyzer(graph, *, source_map: dict[str, bytes]) -> DeadCodeAnalyzer:
    """Analyzer whose marker prepasses read the map (see test_prepass_source_map.py)."""
    return DeadCodeAnalyzer(
        graph,
        git_meta_map=_stale_meta(*[n for n in graph.nodes() if "::" not in str(n)]),
        parsed_files={p: _parsed(p) for p in source_map},
        source_map=source_map,
    )


# ---------------------------------------------------------------------------
# The loader: a package with runtime-dispatch machinery. The dynamic target is
# resolved by a *configured* name, so no literal and no graph edge ever names
# the symbol — the strongest form of the issue.
# ---------------------------------------------------------------------------

_LOADER_SRC = (
    "import importlib\n"
    "for name in serializer_names:\n"
    "    module = importlib.import_module('pkg.serializers')\n"
    "    cls = getattr(module, configured_name)\n"
    "    registry['serializer'] = cls\n"
)


def _registry_graph() -> object:
    """pkg/ has an importlib loader; pkg/serializers.py defines a public class
    reached only as a registry value; other/ is a clean package."""
    return _build_graph(
        nodes={
            "pkg/loader.py": {"is_entry_point": False, "is_test": False, "symbol_count": 2},
            "pkg/serializers.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "JsonSerializer",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "other/serializers.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "XmlSerializer",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[],
    )


def test_registry_target_in_dynamic_package_is_downgraded_not_deletion_ready():
    """The issue's headline: unresolved dynamic use must cap confidence and
    clear ``safe_to_delete`` — while the finding still surfaces."""
    graph = _registry_graph()
    source_map = {
        "pkg/loader.py": _LOADER_SRC.encode(),
        "pkg/serializers.py": b"class JsonSerializer:\n    pass\n",
        "other/serializers.py": b"class JsonSerializer:\n    pass\n",
    }
    report = _analyzer(graph, source_map=source_map).analyze(dict(_EXPORT_ONLY))

    dynamic = _export(report, "JsonSerializer", file_path="pkg/serializers.py")
    assert dynamic.confidence == pytest.approx(RISK_CAP_CONFIDENCE)
    assert not dynamic.safe_to_delete
    assert any("dynamic" in e.lower() for e in dynamic.evidence)


def test_control_without_dynamic_machinery_keeps_high_confidence():
    """Same-named symbol in a clean package: the clamp is per-package, and a
    genuinely unused export still reads as deletion-ready."""
    graph = _registry_graph()
    source_map = {
        "pkg/loader.py": _LOADER_SRC.encode(),
        "pkg/serializers.py": b"class JsonSerializer:\n    pass\n",
        "other/serializers.py": b"class XmlSerializer:\n    pass\n",
    }
    report = _analyzer(graph, source_map=source_map).analyze(dict(_EXPORT_ONLY))

    control = _export(report, "XmlSerializer", file_path="other/serializers.py")
    assert control.confidence == pytest.approx(0.7)
    assert control.safe_to_delete


def test_callback_registration_is_downgraded():
    """A callback appended to a collection from a dynamic-dispatch file — the
    ``callbacks.append(after_commit)`` shape — is the same unresolved case."""
    graph = _build_graph(
        nodes={
            "pkg/events.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "after_commit",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 8,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/hooks.py": {"is_entry_point": False, "is_test": False, "symbol_count": 2},
            "plain/helpers.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "flush_cache",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 8,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[],
    )
    source_map = {
        "pkg/events.py": b"def after_commit(tx):\n    pass\n",
        "pkg/hooks.py": (b"from pkg import events\ncallbacks.append(events.after_commit)\n"),
        "plain/helpers.py": b"def flush_cache():\n    pass\n",
    }
    report = _analyzer(graph, source_map=source_map).analyze(dict(_EXPORT_ONLY))

    dynamic = _export(report, "after_commit", file_path="pkg/events.py")
    assert dynamic.confidence == pytest.approx(RISK_CAP_CONFIDENCE)
    assert not dynamic.safe_to_delete

    control = _export(report, "flush_cache", file_path="plain/helpers.py")
    assert control.confidence == pytest.approx(0.7)
    assert control.safe_to_delete


def test_resolved_dynamic_edge_still_skips_the_symbol():
    """A pinned ``dynamic_uses`` edge is a refutation, not a downgrade: the
    symbol is not flagged at all. Refutation can only remove a finding, never
    create a high-confidence one (monotonicity, part 1)."""
    graph = _build_graph(
        nodes={
            "pkg/serializers.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "JsonSerializer",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/loader.py": {"is_entry_point": False, "is_test": False, "symbol_count": 2},
        },
        edges=[
            ("pkg/loader.py", "pkg/serializers.py", {"edge_type": "dynamic_uses"}),
        ],
    )
    report = _analyzer(graph, source_map={}).analyze(dict(_EXPORT_ONLY))

    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "JsonSerializer" not in names


def test_refutation_is_identity_aware_across_same_named_symbols():
    """The issue's same-named-pair case: two files each define a public
    ``JsonSerializer``, and the loader's ``dynamic_uses`` edge pins only the
    ``pkg`` one. The refutation rescues exactly the pinned candidate; the
    sibling in the clean package is still surfaced — never blanketed by a
    foreign dynamic edge (identity-aware, and the reason the rescue is
    file-level rather than name-level)."""
    graph = _build_graph(
        nodes={
            "pkg/serializers.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "JsonSerializer",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "other/serializers.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "JsonSerializer",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/loader.py": {"is_entry_point": False, "is_test": False, "symbol_count": 2},
        },
        edges=[
            ("pkg/loader.py", "pkg/serializers.py", {"edge_type": "dynamic_uses"}),
        ],
    )
    report = _analyzer(graph, source_map={}).analyze(dict(_EXPORT_ONLY))

    # The pinned candidate is refuted...
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "JsonSerializer" in names  # only the unpinned sibling remains
    finding = _export(report, "JsonSerializer", file_path="other/serializers.py")
    # ...and the sibling keeps its ordinary verdict (stale-commit promotion).
    assert finding.confidence == pytest.approx(0.7)
    assert finding.safe_to_delete


def test_marker_only_downgrades_and_edge_only_skips():
    """The two dynamic signals stay distinct: a marker with no edge downgrades,
    an edge with no marker refutes. A finding cannot gain confidence from
    either (monotonicity, part 2)."""
    marker_graph = _build_graph(
        nodes={
            "pkg/target.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "Target",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/loader.py": {"is_entry_point": False, "is_test": False, "symbol_count": 1},
        },
        edges=[],
    )
    source_map = {
        "pkg/loader.py": b"import importlib\nimportlib.import_module('pkg.target')\n",
        "pkg/target.py": b"class Target:\n    pass\n",
    }
    report = _analyzer(marker_graph, source_map=source_map).analyze(dict(_EXPORT_ONLY))
    finding = _export(report, "Target")
    assert finding.confidence == pytest.approx(RISK_CAP_CONFIDENCE)
    assert not finding.safe_to_delete

    edge_graph = _build_graph(
        nodes={
            "pkg/target.py": {
                "is_entry_point": False,
                "is_test": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "Target",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/loader.py": {"is_entry_point": False, "is_test": False, "symbol_count": 1},
        },
        edges=[("pkg/loader.py", "pkg/target.py", {"edge_type": "dynamic_uses"})],
    )
    edge_report = _analyzer(edge_graph, source_map={}).analyze(dict(_EXPORT_ONLY))
    names = {f.symbol_name for f in edge_report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "Target" not in names

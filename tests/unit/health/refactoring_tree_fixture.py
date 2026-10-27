"""Loader for the checked-in opportunity-composition corpus.

The corpus is a small source tree under
``tests/fixtures/refactoring_corpus/trees``, one directory per archetype, run
through the real analyzer and the real composer. No repository download, no
index, no network, and no hand-written plan payloads: what the golden pins is
what the shipped pipeline produces.

The tree is deliberately not Repowise code. Repowise is the dogfood repo, never
the tuning target, so an archetype has to hold on source the layer has never
been fitted to.

``MANIFEST`` states each archetype's expected answer in prose next to the
directory it lives in, and ``UNCOVERED`` declares the archetypes nobody has
written yet, so a gap reads as a gap rather than as a clean run.

Regenerate the golden with ``REPOWISE_REWRITE_REFACTORING_GOLDEN=1``; never
hand-edit it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .refactoring_corpus_fixture import (  # noqa: F401  (shared regenerate switch)
    CORPUS_DIR,
    REWRITE_ENV,
    load_golden,
    rewrite_requested,
    write_golden,
)

TREES_DIR = CORPUS_DIR / "trees"

# archetype directory -> what the layer is expected to conclude about it.
MANIFEST: dict[str, str] = {
    "orm": (
        "Declarative ORM columns repeat by construction and cannot be lifted into "
        "a function. The gates drop the repetition before it reaches a plan, so "
        "the archetype's answer is silence."
    ),
    "web": (
        "Handlers exist only because a decorator registered them. A lifted span "
        "beside one is mechanical - it moves nothing the router holds - while the "
        "duplication across the three read handlers stays evidence, because "
        "nothing here proves the sites move together."
    ),
    "gopkg": (
        "Three responsibilities in one Go file, over the split threshold. A split "
        "is offered and is a judgment call: the groups cannot be named, and no "
        "split can prove the file carries no build constraint."
    ),
    "tsapp": (
        "A re-export barrel is duplication that is the module's content, and its "
        "two parser modules are a real cross-file clone with no co-change history "
        "behind it. Neither earns a step; the branch-heavy parsers do."
    ),
}

# Archetypes and paths the corpus does not cover, declared rather than silently
# missing, so a run over four trees is never read as coverage of eleven language
# tags or of every classification branch.
UNCOVERED: tuple[str, ...] = (
    "A mechanical structural step of any kind. Extraction is the only mechanical "
    "class: a split cannot prove the source file carries no build constraint "
    "(a Go ``_linux.go`` basename or a ``//go:build`` line), and that fact does "
    "not reach this layer. ``gopkg`` also fails the naming test independently, "
    "because split_file groups by call edges and Go receiver typing does not "
    "resolve method calls.",
    "Any co-change-backed clone. A fixture tree has no git history, so "
    "``co_change_count`` is 0 everywhere and every clone demotes to evidence. "
    "The promotion side of the rule is only observable on a real repository.",
    "Java: an interface-implementing class where Extract Class would break the "
    "declared contract.",
    "C#: partial-class fragments, where a split's symbols already span files.",
    "Rust: a trait impl block, where moving a method changes which trait it "
    "satisfies.",
    "C++: a header/implementation pair, where a split has to move two files in "
    "step.",
    "Generated and vendored paths, where the correct answer is silence "
    "regardless of shape.",
)


def analyze_tree(root: Path) -> Any:
    """Run the shipped ingestion + health pass over *root*. No DB, no git."""
    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    traverser = FileTraverser(root)
    parser = ASTParser()
    graph_builder = GraphBuilder(root)
    parsed_files = []
    for info in traverser.traverse():
        try:
            parsed = parser.parse_file(info, Path(info.abs_path).read_bytes())
        except Exception:
            # A grammar this build lacks is a skipped file, not a failed run.
            continue
        graph_builder.add_file(parsed)
        parsed_files.append(parsed)
    graph_builder.build()
    analyzer = HealthAnalyzer(
        graph_builder.graph(),
        git_meta_map={},
        parsed_files=parsed_files,
        repo_root=root,
    )
    return analyzer.analyze(None)


def compose_tree(root: Path) -> list[dict[str, Any]]:
    """The composed opportunities for one archetype tree, golden-ready."""
    from repowise.core.analysis.health.models import primary_biomarker_by_file
    from repowise.core.analysis.health.refactoring import compose_opportunities

    report = analyze_tree(root)
    opportunities = compose_opportunities(
        getattr(report, "refactoring_suggestions", None) or [],
        primary_biomarker_by_file=primary_biomarker_by_file(report.findings),
    )
    return [_stable(item.as_dict()) for item in opportunities]


def plans_for_tree(root: Path) -> list[dict[str, Any]]:
    """The raw plans behind one tree, for the demotion assertions."""
    report = analyze_tree(root)
    return [
        {
            "refactoring_type": item.refactoring_type,
            "file_path": item.file_path,
            "evidence": dict(item.evidence or {}),
        }
        for item in (getattr(report, "refactoring_suggestions", None) or [])
    ]


def _stable(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop the fields that are a function of the checkout, not the archetype.

    Absolute paths and line numbers move when the fixture is edited for reasons
    the golden is not about; the ids, order, membership, classification and
    ranking are what it exists to pin.
    """
    payload = dict(payload)
    for step in payload.get("steps", []):
        step.pop("line_start", None)
        step.pop("line_end", None)
    return payload


def archetype_roots() -> list[tuple[str, Path]]:
    return [(name, TREES_DIR / name) for name in sorted(MANIFEST) if (TREES_DIR / name).is_dir()]

"""Tests for the Extract Helper refactoring detector (clone dedup).

The detector turns the verified clone pairs the health pass already computed
(``ClonePair`` records, surfaced on ``RefactoringContext.clones``) into a
structured "extract this duplicated block into a shared helper" suggestion.

Fixtures build ``ClonePair`` records directly so the clone geometry — which
files, which line ranges, co-change — is explicit and the expected
occurrences are unambiguous and deterministic.
"""

from __future__ import annotations

from repowise.core.analysis.health.duplication import ClonePair
from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    detect_refactorings,
    registered_detectors,
)
from repowise.core.analysis.health.refactoring.extract_helper import (
    _MAX_SNIPPET_LINES,
    _common_directory,
    _is_declaration_only,
    _merge_ranges_per_file,
    _suggested_name,
)


class _DryFinding:
    """Minimal HealthFindingData-like stand-in for a dry_violation finding."""

    def __init__(self, start: int, end: int, impact: float):
        self.biomarker_type = "dry_violation"
        self.line_start = start
        self.line_end = end
        self.health_impact = impact
        self.function_name = None
        self.details = {}


def _pair(
    file_a: str,
    file_b: str,
    a_start: int,
    a_end: int,
    b_start: int,
    b_end: int,
    *,
    token_count: int = 50,
    co_change: int = 0,
) -> ClonePair:
    return ClonePair(
        file_a=file_a,
        file_b=file_b,
        a_start_line=a_start,
        a_end_line=a_end,
        b_start_line=b_start,
        b_end_line=b_end,
        token_count=token_count,
        co_change_count=co_change,
    )


def _ctx(
    file_path: str,
    clones: list[ClonePair],
    *,
    findings: list | None = None,
    community_label_map: dict[str, str] | None = None,
    source_lines: list[str] | None = None,
) -> RefactoringContext:
    return RefactoringContext(
        file_path=file_path,
        language="python",
        nloc=200,
        classes=[],
        findings=findings or [],
        dependents_count=0,
        clones=clones,
        community_label_map=community_label_map or {},
        source_lines=source_lines,
    )


# ---- registration --------------------------------------------------------


def test_detector_registered():
    assert "extract_helper" in [d.name for d in registered_detectors()]


def test_silent_without_clones():
    assert detect_refactorings(_ctx("a/x.py", [])) == []


# ---- cross-file extraction + canonical anchor ----------------------------


def test_emits_for_cross_file_clone():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    s = sugs[0]
    assert s.file_path == "pkg/a.py"
    assert s.target_symbol == "a.py:10-25"
    assert s.plan["occurrences"] == [
        {"file": "pkg/a.py", "line_start": 10, "line_end": 25},
        {"file": "pkg/b.py", "line_start": 40, "line_end": 55},
    ]
    assert s.evidence["occurrence_count"] == 2
    assert s.evidence["duplicated_lines"] == 16
    assert s.evidence["is_intra_file"] is False
    assert s.blast_radius == {
        "files": ["pkg/b.py"],
        "file_count": 1,
        "co_change_count": 0,
    }


def test_canonical_anchor_dedups():
    # The same pair seen from the non-anchor file (b > a) yields nothing, so a
    # clone set is suggested exactly once.
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/b.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ]
    assert sugs == []


def test_transitive_clone_set_one_suggestion():
    # A clones with B and C; from A (the min path) both partners are visible →
    # one suggestion listing all three sites.
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "pkg/c.py", 10, 25, 70, 85),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    files = [o["file"] for o in sugs[0].plan["occurrences"]]
    assert files == ["pkg/a.py", "pkg/b.py", "pkg/c.py"]
    assert sugs[0].blast_radius["file_count"] == 2


def test_intra_file_clone():
    pair = _pair("pkg/a.py", "pkg/a.py", 10, 25, 60, 75)
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    s = sugs[0]
    assert s.evidence["is_intra_file"] is True
    assert s.plan["occurrences"] == [
        {"file": "pkg/a.py", "line_start": 10, "line_end": 25},
        {"file": "pkg/a.py", "line_start": 60, "line_end": 75},
    ]
    assert s.blast_radius["files"] == []


def test_overlapping_windows_collapse_to_one_site():
    # The clone detector emits a block as several offset windows; they must
    # coalesce into one occurrence per file, not read as many sites.
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 8, 35, 40, 67),
        _pair("pkg/a.py", "pkg/b.py", 9, 36, 41, 68),
        _pair("pkg/a.py", "pkg/b.py", 22, 30, 54, 62),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    assert sugs[0].plan["occurrences"] == [
        {"file": "pkg/a.py", "line_start": 8, "line_end": 36},
        {"file": "pkg/b.py", "line_start": 40, "line_end": 68},
    ]
    assert sugs[0].evidence["occurrence_count"] == 2


def test_merge_ranges_per_file_helper():
    merged = _merge_ranges_per_file(
        [("a.py", 8, 35), ("a.py", 9, 36), ("a.py", 22, 28), ("a.py", 60, 75), ("b.py", 1, 9)]
    )
    assert merged == [("a.py", 8, 36), ("a.py", 60, 75), ("b.py", 1, 9)]


def test_two_distinct_blocks_in_one_file():
    # Two non-overlapping anchor regions → two separate suggestions.
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "pkg/c.py", 100, 120, 5, 25),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 2
    assert {s.line_start for s in sugs} == {10, 100}


# ---- gates ---------------------------------------------------------------


def test_below_min_lines_skipped():
    # 6-line clone — below the 8-line helper floor.
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 15, 40, 45)
    assert [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] == []


def test_test_file_occurrences_dropped():
    # A clone shared only with a test file collapses to one real site → skip.
    pair = _pair("pkg/a.py", "tests/test_a.py", 10, 25, 40, 55)
    assert [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] == []


def test_test_support_occurrences_dropped():
    # Extracting a shared helper out of conftest.py is the same bad advice as
    # extracting one out of a test, so support counts as test material (#1103).
    pair = _pair("pkg/a.py", "tests/conftest.py", 10, 25, 40, 55)
    assert [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] == []


def test_production_path_containing_the_word_test_is_kept():
    # `src/latest/` is production: an unanchored match would drop this
    # occurrence and collapse a real two-site clone to one.
    pair = _pair("pkg/a.py", "src/latest/api.py", 10, 25, 40, 55)
    assert [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] != []


def test_test_file_dropped_but_real_sites_kept():
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "tests/test_a.py", 10, 25, 5, 20),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    files = [o["file"] for o in sugs[0].plan["occurrences"]]
    assert files == ["pkg/a.py", "pkg/b.py"]


def test_generated_migration_occurrences_dropped():
    # Migration boilerplate duplicates heavily but is never refactored — a
    # clone confined to migration files yields no suggestion.
    pair = _pair(
        "core/alembic/versions/0001_a.py",
        "core/alembic/versions/0002_b.py",
        10,
        25,
        10,
        25,
    )
    assert [
        s
        for s in detect_refactorings(_ctx("core/alembic/versions/0001_a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] == []


def test_disabled_detector_yields_nothing():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    assert detect_refactorings(_ctx("pkg/a.py", [pair]), disabled=["extract_helper"]) == []


# ---- impact + confidence -------------------------------------------------


def test_impact_from_overlapping_dry_violation():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    findings = [_DryFinding(12, 22, 1.8)]
    sugs = detect_refactorings(_ctx("pkg/a.py", [pair], findings=findings))
    s = next(s for s in sugs if s.refactoring_type == "extract_helper")
    assert s.impact_delta == 1.8


def test_no_impact_when_finding_disjoint():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    findings = [_DryFinding(200, 210, 1.8)]
    sugs = detect_refactorings(_ctx("pkg/a.py", [pair], findings=findings))
    s = next(s for s in sugs if s.refactoring_type == "extract_helper")
    assert s.impact_delta == 0.0


def test_confidence_high_when_actively_co_changed():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55, co_change=4)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.confidence == "high"
    assert s.evidence["co_change_count"] == 4


def test_confidence_medium_when_dormant():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55, co_change=0)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.confidence == "medium"


# ---- suggested site ------------------------------------------------------


def _site_for(community_label_map: dict[str, str] | None) -> dict:
    """The suggested site for one fixed cross-package clone, under *community_label_map*.

    The occurrences deliberately live in different top-level packages, so the
    only honest shared directory is the shallow ``pkg`` -- the case where a
    community label reads nicer and is wrong.
    """
    pair = _pair("pkg/api/a.py", "pkg/core/b.py", 10, 25, 40, 55)
    s = next(
        s
        for s in detect_refactorings(
            _ctx("pkg/api/a.py", [pair], community_label_map=community_label_map)
        )
        if s.refactoring_type == "extract_helper"
    )
    return s.plan


def test_a_container_directory_is_not_a_site():
    """``pkg`` holds no occurrence, only other packages: naming it as the
    helper's home names a place nothing can go. This is the small-scale twin
    of ``suggested_site: "packages"`` on the real index."""
    plan = _site_for(None)
    assert plan["suggested_site"] == {"directory": None}
    assert plan["suggested_name"] is None


def test_suggested_site_ignores_a_hostile_community_label():
    """The load-bearing property: the plan no longer depends on which write
    path produced it.

    ``community_label_map`` is populated by the full-index path alone -- the incremental,
    re-score and ``repowise health`` paths leave it empty -- so a label-derived
    site made the payload's namespace a function of the last writer. Here the
    label ``ui`` is on *neither* occurrence's path, which is the shape measured
    on the real index (905 of 905 labelled plans named a directory no
    occurrence lived in). A detector that still read it would answer ``ui``.
    """
    hostile = {"pkg/api/a.py": "ui", "pkg/core/b.py": "ui"}
    assert _site_for(hostile) == _site_for(None)
    assert "module" not in _site_for(hostile)["suggested_site"]
    assert _site_for(hostile)["suggested_name"] is None


def test_suggested_site_prefers_the_deepest_shared_directory():
    pair = _pair("pkg/sub/a.py", "pkg/sub/b.py", 10, 25, 40, 55)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/sub/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.plan["suggested_site"] == {"directory": "pkg/sub"}


def test_common_directory_helper():
    assert _common_directory(["a/b/x.py", "a/b/y.py"]) == "a/b"
    # Shared prefix only: no occurrence lives directly in "a".
    assert _common_directory(["a/b/x.py", "a/c/y.py"]) is None
    # One does, so the directory is a real home for the helper.
    assert _common_directory(["a/x.py", "a/c/y.py"]) == "a"
    assert _common_directory(["a/x.py", "b/y.py"]) is None
    assert _common_directory(["x.py", "a/y.py"]) is None


# ---- snippet + suggested name --------------------------------------------


def _numbered_source(n: int) -> list[str]:
    # 1-indexed content so a slice is easy to assert: line k reads "line k".
    return [f"line {i}" for i in range(1, n + 1)]


def test_snippet_sliced_from_anchor_region():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    s = next(
        s
        for s in detect_refactorings(
            _ctx("pkg/a.py", [pair], source_lines=_numbered_source(80))
        )
        if s.refactoring_type == "extract_helper"
    )
    assert s.plan["snippet_start_line"] == 10
    assert s.plan["snippet_truncated"] is False
    lines = s.plan["snippet"].split("\n")
    assert lines[0] == "line 10"
    assert lines[-1] == "line 25"
    assert len(lines) == 16


def test_snippet_none_without_source():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.plan["snippet"] is None
    assert s.plan["snippet_start_line"] is None
    assert s.plan["snippet_truncated"] is False


def test_snippet_capped_and_flagged():
    # A 60-line block clips to the cap and flags it.
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 69, 100, 159)
    s = next(
        s
        for s in detect_refactorings(
            _ctx("pkg/a.py", [pair], source_lines=_numbered_source(200))
        )
        if s.refactoring_type == "extract_helper"
    )
    assert s.plan["snippet_truncated"] is True
    assert len(s.plan["snippet"].split("\n")) == _MAX_SNIPPET_LINES


def test_snippet_clamped_to_short_file():
    # Clone range runs past EOF (a stale-ish range); clamp, never IndexError.
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    s = next(
        s
        for s in detect_refactorings(
            _ctx("pkg/a.py", [pair], source_lines=_numbered_source(18))
        )
        if s.refactoring_type == "extract_helper"
    )
    lines = s.plan["snippet"].split("\n")
    assert lines[0] == "line 10"
    assert lines[-1] == "line 18"


def test_suggested_name_from_directory():
    pair = _pair("pkg/sub/a.py", "pkg/sub/b.py", 10, 25, 40, 55)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/sub/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.plan["suggested_name"] is None


def test_suggested_name_is_absent_rather_than_invented():
    """Where a block lives says nothing about what it does, and every block
    under one directory got the same name -- six plans on this repo's index
    were all ``persistence_helper``. Absent is the honest answer."""
    assert _suggested_name({"directory": "api"}) is None
    assert _suggested_name({"directory": "web/api-client"}) is None
    assert _suggested_name({"directory": None}) is None


def test_suggested_name_ignores_a_legacy_community_label():
    """A plan stored before the community label was dropped still carries
    ``module``. It is the key that produced names like ``repowise_helper`` --
    the repo naming its own helper -- so it must not be revived as a fallback;
    ``directory`` was the correct value on those rows and is what wins."""
    assert _suggested_name({"module": "core", "directory": "pkg/sub"}) is None
    # Even with no directory at all, the label is not consulted.
    assert _suggested_name({"module": "repowise", "directory": None}) is None


# ---- determinism ---------------------------------------------------------


def test_deterministic_and_stable_order():
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "pkg/c.py", 100, 120, 5, 25),
    ]
    findings = [_DryFinding(100, 120, 3.0), _DryFinding(10, 25, 1.0)]
    a = detect_refactorings(_ctx("pkg/a.py", clones, findings=findings))
    b = detect_refactorings(_ctx("pkg/a.py", clones, findings=findings))
    a = [s for s in a if s.refactoring_type == "extract_helper"]
    b = [s for s in b if s.refactoring_type == "extract_helper"]
    # Bigger recovered impact first.
    assert [s.line_start for s in a] == [100, 10]
    assert [(s.target_symbol, s.plan) for s in a] == [(s.target_symbol, s.plan) for s in b]


# ---- declaration-only blocks are not extractable -------------------------
#
# A clone made entirely of declarations has no behaviour to share, so a plan
# proposing to extract it is advice no editor can follow. Three real shapes
# from this repo's own index motivated the gate, and the worst of them arrived
# in the most-trusted slot in the payload (highest occurrence count, biggest
# blast radius): an `x49` import block and an `x32` one.
#
# The unit cases below cannot be revert-tested (the helper is new, so a revert
# cannot import it); the `detect_refactorings` cases can, and do — without the
# gate the detector emits a plan for every one of them.


def _decl_source(body: list[str], *, at: int, total: int = 80) -> list[str]:
    """A file whose lines *at*..*at+len(body)* are *body*, rest inert code."""
    lines = [f"    value_{i} = compute_{i}(seed)" for i in range(1, total + 1)]
    lines[at - 1 : at - 1 + len(body)] = body
    return lines


_IMPORT_BLOCK = [
    "from repowise.cli.helpers import (",
    "    clear_update_queued,",
    "    console,",
    "    consume_update_pending,",
    "    ensure_repowise_dir,",
    "    find_workspace_root,",
    "    get_head_commit,",
    "    load_config,",
    "    save_state,",
    ")",
]

_SIGNATURE_BLOCK = [
    "def update_command(",
    "    path: str | None,",
    "    provider_name: str | None,",
    "    since: str | None,",
    "    dry_run: bool,",
    "    workspace: bool,",
    "    index_only: bool = False,",
    "    concurrency: int = 10,",
    ")",
]

_FIELD_BLOCK = [
    "    name: str = ''",
    "    items: list[str] = field(default_factory=list)",
    "    mapping: dict[str, int] = field(default_factory=dict)",
    "    alternatives: list[str] = field(default_factory=list)",
    "    count: int = 0",
    "    enabled: bool = False",
    "    label: str = 'none'",
    "    weight: float = 1.0",
]

_REAL_BLOCK = [
    "    if result is not None:",
    "        try:",
    "            save_knowledge_graph_json(repo_path, result)",
    "        except Exception as exc:",
    "            degraded.append(exc)",
    "    emitter.done(ok=True, pages_generated=0)",
    "    consume_update_pending(repo_path, head)",
    "    total = compute_total(rows)",
    "    scaled = total * weight",
]


def _plans_for(body: list[str]):
    """Plans the detector emits for a cross-file clone whose block is *body*."""
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 10 + len(body) - 1, 40, 40 + len(body) - 1)
    return [
        s
        for s in detect_refactorings(
            _ctx("pkg/a.py", [pair], source_lines=_decl_source(body, at=10))
        )
        if s.refactoring_type == "extract_helper"
    ]


def test_import_block_clone_emits_no_plan():
    """You cannot extract `from x import (...)` into a shared helper."""
    assert _plans_for(_IMPORT_BLOCK) == []


def test_function_signature_clone_emits_no_plan():
    """Two functions whose parameter lists agree are not duplicated behaviour."""
    assert _plans_for(_SIGNATURE_BLOCK) == []


def test_dataclass_field_run_emits_no_plan():
    """Unrelated dataclasses sharing a run of `field(default_factory=...)`
    declarations share a shape, not code. This is the case filed as C6."""
    assert _plans_for(_FIELD_BLOCK) == []


def test_real_duplicated_code_still_emits_a_plan():
    """The gate must stay conservative: a false negative silently drops real
    duplication, which is the more expensive mistake."""
    plans = _plans_for(_REAL_BLOCK)
    assert len(plans) == 1
    assert plans[0].plan["duplicated_lines"] == len(_REAL_BLOCK)


def test_gate_abstains_when_no_source_is_threaded():
    """No source means no evidence to reject on, so the plan survives — the
    detector's pre-existing behaviour for non-clone / unreadable files."""
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    plans = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair], source_lines=None))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(plans) == 1


def test_declaration_only_unit_cases():
    from repowise.core.analysis.health.refactoring.extract_helper import (
        _is_declaration_only,
    )

    # Rejected: nothing to extract.
    assert _is_declaration_only(_IMPORT_BLOCK)
    assert _is_declaration_only(_SIGNATURE_BLOCK)
    assert _is_declaration_only(_FIELD_BLOCK)
    assert _is_declaration_only(["    ALPHA = 'a'", "    BETA = 'b'", "    GAMMA = 'c'"])
    assert _is_declaration_only(["import os", "import sys", "from x import y"])
    assert _is_declaration_only(['export { A } from "./a";', 'export * from "./b";'])
    assert _is_declaration_only(["    # only a comment", "", "    # another"])

    # Kept: real behaviour, by three different routes.
    assert not _is_declaration_only(["    if x:", "        go()"])  # control flow
    assert not _is_declaration_only(["    emit(a)", "    emit(b)"])  # statement calls
    assert not _is_declaration_only(["    t = compute(x)"])  # computed assignment
    assert not _is_declaration_only(["    total = sum_rows(rows)", "    n = len(total)"])

    # A type annotation does not make code a declaration. This is the shape an
    # adversarial review caught the first version of the gate dropping, and it
    # is one of the most common lines in typed Python and TypeScript, so it is
    # the highest-frequency false negative the gate could have had.
    assert not _is_declaration_only(
        [
            "    result: int = calculate_total(items, tax_rate)",
            "    status: str = fetch_status(connection)",
        ]
    )
    # What separates it from a field run is the *constructor*, not the call: a
    # closed vocabulary of field declarations is still a declaration.
    assert _is_declaration_only(
        [
            "    items: list[str] = field(default_factory=list)",
            "    id: int = Column(Integer)",
            "    kids: list = relationship('K')",
        ]
    )

    # A leading `*` is a javadoc continuation in one language and a pointer
    # dereference in another. Stripping it unconditionally emptied the line list
    # and read as "nothing but declarations", defanging the gate for C and Go.
    assert not _is_declaration_only(["*x = compute_value(a, b);", "*y = another(c);"])
    assert _is_declaration_only(["    * @param foo the thing", "    * @returns nothing"])

    # Single-word control flow carries no other token to recognise it by, so it
    # has to be in the keyword list or it reads as a bare identifier.
    assert not _is_declaration_only(["    continue", "    continue"])
    assert not _is_declaration_only(["    break", "    break"])
    assert not _is_declaration_only(["    defer f.Close()", "    defer g.Close()"])


# ---- symbol-boundary gating ----------------------------------------------


def _graph_with_symbols(spans: dict[str, list[tuple[str, int, int]]]):
    """A minimal repo graph: files defining symbols with declaration spans."""
    import networkx as nx

    graph = nx.DiGraph()
    for file_path, symbols in spans.items():
        graph.add_node(file_path, node_type="file")
        for name, start, end in symbols:
            sid = f"{file_path}::{name}"
            graph.add_node(
                sid,
                node_type="symbol",
                kind="class",
                name=name,
                file_path=file_path,
                start_line=start,
                end_line=end,
            )
            graph.add_edge(file_path, sid, edge_type="defines")
    return graph


def _ctx_with_graph(file_path, clones, graph, source_lines=None):
    return RefactoringContext(
        file_path=file_path,
        language="python",
        nloc=200,
        classes=[],
        findings=[],
        dependents_count=0,
        clones=clones,
        graph=graph,
        community_label_map={},
        source_lines=source_lines,
    )


def _helper_plans(ctx):
    return [s for s in detect_refactorings(ctx) if s.refactoring_type == "extract_helper"]


def test_clone_inside_one_declaration_still_emits():
    graph = _graph_with_symbols(
        {"pkg/a.py": [("Alpha", 1, 40)], "pkg/b.py": [("Beta", 30, 70)]}
    )
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    assert len(_helper_plans(_ctx_with_graph("pkg/a.py", [pair], graph))) == 1


def test_clone_crossing_a_declaration_boundary_is_dropped():
    # The a.py occurrence ends inside the next class, exactly the shape the
    # repo's own top-ranked plan had over SQLAlchemy models.
    graph = _graph_with_symbols(
        {"pkg/a.py": [("Alpha", 1, 20), ("Beta", 21, 60)], "pkg/b.py": [("Gamma", 30, 70)]}
    )
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    assert _helper_plans(_ctx_with_graph("pkg/a.py", [pair], graph)) == []


def test_clone_swallowing_whole_declarations_is_dropped():
    graph = _graph_with_symbols(
        {"pkg/a.py": [("Alpha", 12, 18)], "pkg/b.py": [("Gamma", 30, 70)]}
    )
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    assert _helper_plans(_ctx_with_graph("pkg/a.py", [pair], graph)) == []


def test_gate_abstains_without_symbol_facts():
    # A language whose symbols the graph does not carry keeps its plans.
    graph = _graph_with_symbols({"pkg/b.py": [("Gamma", 30, 70)]})
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    assert len(_helper_plans(_ctx_with_graph("pkg/a.py", [pair], graph))) == 1


# ---- ORM / dataclass boilerplate: the correct answer is no suggestion -----


_ORM_COLUMNS = [
    "    id: Mapped[str] = mapped_column(",
    "        String(32), primary_key=True, default=new_uuid",
    "    )",
    "    created_at: Mapped[datetime] = mapped_column(",
    "        DateTime, nullable=False, default=utcnow",
    "    )",
    "    updated_at: Mapped[datetime] = mapped_column(",
    "        DateTime, nullable=False, default=utcnow",
    "    )",
    "    repository_id: Mapped[str] = mapped_column(",
    '        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False',
    "    )",
]

_DATACLASS_FIELDS = [
    "    names: list[str] = field(",
    "        default_factory=list",
    "    )",
    "    counts: dict[str, int] = field(",
    "        default_factory=dict",
    "    )",
    "    label: str = field(",
    '        default=""',
    "    )",
    "    tags: set[str] = field(",
    "        default_factory=set",
    "    )",
]


def _boilerplate_plans(block: list[str]) -> list:
    source = ["" for _ in range(9)] + block
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 9 + len(block), 40, 39 + len(block))
    return _helper_plans(
        _ctx_with_graph(
            "pkg/a.py",
            [pair],
            _graph_with_symbols({"pkg/a.py": [("Model", 1, 400)], "pkg/b.py": [("Other", 1, 400)]}),
            source_lines=source,
        )
    )


def test_multiline_orm_columns_emit_no_plan():
    assert _boilerplate_plans(_ORM_COLUMNS) == []


def test_multiline_dataclass_fields_emit_no_plan():
    assert _boilerplate_plans(_DATACLASS_FIELDS) == []


_TABLE_BODY = [
    '    __tablename__ = "wiki_symbols"',
    "",
    "    id: Mapped[str] = mapped_column(String(32), primary_key=True)",
    "    repository_id: Mapped[str] = mapped_column(",
    '        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False',
    "    )",
    "    file_path: Mapped[str] = mapped_column(Text, nullable=False)",
    "",
    "    __table_args__ = (",
    '        UniqueConstraint("repository_id", "file_path", name="uq_wiki_symbol"),',
    '        Index("ix_wiki_symbols_repo", "repository_id"),',
    "    )",
]


def test_declarative_table_body_emits_no_plan():
    assert _boilerplate_plans(_TABLE_BODY) == []


# ---- logical-line joining must not swallow real behaviour ----------------


def test_brace_language_bodies_are_not_declarations():
    """``{`` opens a body, not a continuation. Joining on it welded a whole
    function body into one line that then read as a bare signature."""
    go_body = ["func setup() {", "    x := compute(a, b)", "    cache.Store(x)", "}"]
    ts_body = ["function setup() {", "  const x = compute(a, b);", "  cache.store(x);", "}"]
    assert not _is_declaration_only(go_body)
    assert not _is_declaration_only(ts_body)


def test_a_bracket_inside_a_string_does_not_absorb_the_next_statement():
    block = [
        "handler: Any = mapped_column(",
        '    String(32), doc="see note (ref"',
        ")",
        "do_side_effect(handler)",
    ]
    assert not _is_declaration_only(block)


def test_a_one_line_body_is_not_a_signature():
    block = ["def compute(a, b): total = a + b; log(total)", "def other(c): v = c * 2; log(v)"]
    assert not _is_declaration_only(block)


def test_a_signature_joined_back_whole_is_still_a_declaration():
    block = [
        "def update_command(",
        "    path: str | None,",
        "    dry_run: bool,",
        ") -> None:",
        "def run_update(",
        "    path: str | None,",
        "    dry_run: bool,",
        ") -> None:",
    ]
    assert _is_declaration_only(block)

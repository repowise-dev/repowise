"""Which finding types a surface may show, and on what evidence.

One switch per finding type — a dead-code ``kind`` or a health
``biomarker_type``; the two families share no names — so a type that has not
earned its place can be taken off every surface at once, without deleting the
analyzer that produces it. Visibility only: analyzers still run, rows are still
persisted and scored, and the raw CLI analysis still reports them.

- ``validated``: shown everywhere. A type absent from :data:`REGISTRY` is
  validated — it shipped before measurement existed and stays on until a
  measurement says otherwise.
- ``provisional``: shown only when a caller asks for it by name (or opts into
  unverified types), and then labelled :data:`UNVERIFIED_LABEL`.
- ``hidden``: never shown on a default surface (overview, priorities, MCP,
  REST lists, wiki prompts). Surfaces may report how many were held back.

A type moves back to ``validated`` only when its measured precision clears the
family floor; the measurement fields record the evidence either way.

:data:`LANGUAGE_GATES` is the same switch for one layer on one language: a
layer an audit measured below the bar on a language is kept off every default
surface for files of that language, while other languages keep it. The opt-in
is the same as for provisional types (:data:`UNVERIFIED_LABEL`). A gated row is
still computed, stored and addressable by id, so the cell stays measurable and
ungating it is deleting its row here after a re-audit clears it. A row in the
``preview`` state is served by default and counted as served, labelled with its
measurement (:func:`preview_note`) instead of held back. A language with no row
is served unlabelled.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any, Literal

from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE

FindingStatus = Literal["validated", "provisional", "hidden"]

#: The label a provisional finding carries wherever it is shown.
UNVERIFIED_LABEL = "unverified"


@dataclass(frozen=True)
class FindingTypeStatus:
    status: FindingStatus
    #: Measured precision and its Wilson 95% lower bound, or ``None`` when the
    #: type has not been measured.
    precision: float | None = None
    ci_low: float | None = None
    #: What the measurement ran on, in words (no repository names).
    corpus: str | None = None
    #: ISO date of the measurement.
    measured_on: str | None = None
    #: One line saying why the type is not ``validated``.
    reason: str | None = None


_VALIDATED = FindingTypeStatus("validated")

_LABELLED = "hand-labelled findings from one TS/Python monorepo"

REGISTRY: dict[str, FindingTypeStatus] = {
    "unused_internal": FindingTypeStatus(
        "hidden",
        precision=0.005,
        corpus=f"{_LABELLED}, 1,511 in all",
        measured_on="2026-09-29",
        reason=(
            "Measured on TS/Python code: private-symbol findings were almost all "
            "false, because same-file uses leave no 'reads' edge there."
        ),
    ),
    "duplicated_assertion_block": FindingTypeStatus(
        "hidden",
        corpus=f"{_LABELLED}, 1,168 in all",
        measured_on="2026-09-29",
        reason="Clones are matched on token shape only, so unrelated assertions pair up.",
    ),
    # The failing subset is clones over import blocks and data literals. A
    # ClonePair carries only line spans, not the token kinds that would tell
    # that subset apart, so the whole type is hidden until clone detection
    # drops those windows itself (upgrade path: filter in the tokenizer, then
    # re-measure and flip this back).
    "dry_violation": FindingTypeStatus(
        "hidden",
        corpus=_LABELLED,
        measured_on="2026-09-29",
        reason="Flags duplicated import blocks and data literals as design duplication.",
    ),
}


#: The serving layers a gate can apply to. ``health`` is every health finding
#: (performance findings included); ``refactoring`` is plans and the
#: opportunities composed from them; ``performance`` is performance
#: opportunities; ``dead_code`` is every dead-code finding, safe-to-delete or not.
GateLayer = Literal["health", "refactoring", "performance", "dead_code"]

#: ``gated`` holds the layer back on the language (opt-in to see it);
#: ``preview`` serves it by default, labelled with its measurement.
GateState = Literal["gated", "preview"]


@dataclass(frozen=True)
class LanguageGate:
    layer: GateLayer
    #: An ``EXTENSION_TO_LANGUAGE`` tag; a file's extension decides its language.
    language: str
    #: The share the judged sample scored on ``measure``, ``None`` when nothing
    #: was served to judge or the evidence was not a judged sample. A gated
    #: row's served text never quotes it; a preview row's note does.
    precision: float | None
    #: What the measurement ran on, in words (no repository names).
    corpus: str
    #: ISO date of the measurement.
    measured_on: str
    #: Why the layer falls short there, for maintainers; served text never
    #: quotes it (a gated row says only that it is held back pending
    #: verification).
    cause: str
    #: The kinds gated, or ``None`` for every kind of the layer.
    kinds: frozenset[str] | None = None
    state: GateState = "gated"
    #: What ``precision`` measured (``precision`` or ``not wrong``), as the preview note words it.
    measure: str = "precision"


_AUDIT_DATE = "2026-10-02"
_REAUDIT_DATE = "2026-10-15"
_PREVIEW = "preview"
_NO_PLAN = "No judged plan was acceptable as written."
_NOT_A_COST = "Most judged opportunities were not real costs."
_FEW_PLANS = "Most judged plans were not acceptable as written."
_NONE_SERVED = "No opportunity was served to judge."
_IN_MEMORY = "In-memory lookups are read as database calls."
_ONE_PLAN = "One judged plan was not acceptable as written."
_NOT_WRONG = "not wrong"
_VENDORED_HEADER = "A vendored single-header library is not classed vendored."

# Each row covers a whole layer on one language (``kinds=None``). Precision is
# the share of true positives in a judged sample (debatable counted as a miss)
# unless the row names another measure. Columns: layer, language, precision,
# corpus, cause, then optionally the state, its measurement date (default
# gated, ``_AUDIT_DATE``) and the measure. ``_PREVIEW`` serves a row labelled
# with its measurement, ``"gated"`` in its place holds the layer back again,
# and deleting the row ungates it.
_GateRow = (
    tuple[GateLayer, str, float | None, str, str]
    | tuple[GateLayer, str, float | None, str, str, GateState, str]
    | tuple[GateLayer, str, float | None, str, str, GateState, str, str]
)
_GATE_ROWS: tuple[_GateRow, ...] = (
    # Health ships on a not-wrong bar (debatable counts as not wrong): Java,
    # C# and C cleared 0.90 on every corpus on 2026-10-15 and are ungated.
    ("health", "cpp", 0.825, "the lower of 2 C++ repos, 40 judged each", _VENDORED_HEADER, _PREVIEW, _REAUDIT_DATE, _NOT_WRONG),
    ("refactoring", "java", 0.22, "55 judged plans from 2 Java repos", _FEW_PLANS, _PREVIEW, _REAUDIT_DATE),
    ("refactoring", "csharp", 0.83, "6 judged plans from 1 C# repo", _ONE_PLAN, _PREVIEW, _REAUDIT_DATE),
    ("refactoring", "c", 0.0, "1 judged plan from 1 C repo", _NO_PLAN, _PREVIEW, _REAUDIT_DATE),
    ("refactoring", "cpp", 0.33, "3 judged plans from 1 C++ repo", _FEW_PLANS, _PREVIEW, _REAUDIT_DATE),
    ("performance", "java", 0.0, "1 judged opportunity from 4 Java repos", _IN_MEMORY, _PREVIEW, _REAUDIT_DATE),
    ("performance", "csharp", 0.0, "1 judged opportunity from 2 C# repos", _NOT_A_COST, _PREVIEW, _REAUDIT_DATE),
    ("performance", "c", None, "no served opportunities on 2 C repos", _NONE_SERVED, _PREVIEW, _REAUDIT_DATE),
    ("performance", "cpp", None, "no served opportunities on 2 C++ repos", _NONE_SERVED, _PREVIEW, _REAUDIT_DATE),
    # Dead code: pooled precision, with the lowest corpus in the corpus words.
    ("dead_code", "java", 0.37, "30 judged findings from 3 Java repos (lowest 0%)", "Android manifest and layout uses are unseen.", _PREVIEW, _REAUDIT_DATE),
    ("dead_code", "csharp", 0.26, "80 judged findings from 2 C# repos (lowest 5%)", "Custom MEF export attributes and C# 14 extension blocks are unseen.", _PREVIEW, _REAUDIT_DATE),
    ("dead_code", "c", 0.66, "35 judged findings from 2 C repos (lowest 0%)", "P/Invoke exports are unseen.", _PREVIEW, _REAUDIT_DATE),
    ("dead_code", "cpp", 0.68, "25 judged findings from 1 C++ repo", "Build-listed translation units and macro uses are unseen.", _PREVIEW, _REAUDIT_DATE),
    ("dead_code", "kotlin", 0.18, "34 judged findings from 2 Kotlin repos (lowest 0%)", "DI-bound classes are not roots.", _PREVIEW, _REAUDIT_DATE),
    ("dead_code", "swift", 0.44, "71 judged findings from 2 Swift repos (lowest 10%)", "Cross-module function and extension-member uses are unseen.", _PREVIEW, _REAUDIT_DATE),
)


def _row_gate(
    layer: GateLayer,
    language: str,
    precision: float | None,
    corpus: str,
    cause: str,
    state: GateState = "gated",
    measured_on: str = _AUDIT_DATE,
    measure: str = "precision",
) -> LanguageGate:
    return LanguageGate(
        layer, language, precision, corpus, measured_on, cause, state=state, measure=measure
    )


LANGUAGE_GATES: tuple[LanguageGate, ...] = tuple(_row_gate(*row) for row in _GATE_ROWS)


#: Why a health finding is held back by the file it sits on rather than by its
#: language: vendored and generated code is not the reader's to change, and a
#: history marker on a test file describes how the test changes, not a defect
#: in it. On any other file a history marker is context (it orders hotspots and
#: Fix first) rather than a finding to list. A refactoring plan or opportunity on a test file is held back too:
#: Fix first never offers one, and no other plan surface does either. Read from
#: the file's stored ``code_origin`` and ``is_test``, so it holds on every
#: language, with the same opt-in as a language gate.
FILE_HOLDS: dict[str, str] = {
    "vendored": "Held back on vendored files: the code is maintained upstream.",
    "generated": "Held back on generated files: change what generates them instead.",
    "test_history": "History markers held back on test files: they describe change, not a defect.",
    "history": "History markers are context: they order hotspots and Fix first, not the defect list.",
    "test": "Plans held back on test files: tests change with the code they cover.",
}

#: The stored ``code_origin`` values a health finding is never served on.
HELD_ORIGINS: tuple[str, ...] = ("vendored", "generated")

#: The stored ``code_origin`` values the area map leaves out until the reader
#: brings them back: tests, docs examples and build files, plus everything no
#: health finding is served on. Prose docs have no health row, so never appear.
MAP_HELD_ORIGINS: tuple[str, ...] = ("test", "docs_example", "build", *HELD_ORIGINS)


def stored_origin(code_origin: Any, is_test: Any) -> str:
    """A row's origin, reading a row stamped before ``code_origin`` off ``is_test``."""
    if isinstance(code_origin, str) and code_origin:
        return code_origin
    return "test" if is_test is True else "production"


def map_held(include: Iterable[str]) -> frozenset[str]:
    """The origins the map holds out when the reader has brought back *include*."""
    return frozenset(MAP_HELD_ORIGINS) - frozenset(include)


def history_kinds() -> frozenset[str]:
    """The health markers read from git history alone (the history holds)."""
    # Deferred: the health package imports this module.
    from repowise.core.analysis.health.scoring import history_biomarkers

    return history_biomarkers()


def file_hold(
    layer: GateLayer, kind: Any, code_origin: Any, is_test: Any, *, hold_history: bool = True
) -> str | None:
    """The :data:`FILE_HOLDS` key holding back a *layer* row of *kind* on a file
    with that stored origin and test flag, or ``None``. ``hold_history=False``
    keeps history markers on production files (shown.py's flag of that name)."""
    if layer == "refactoring":
        return "test" if is_test is True else None
    if layer != "health":
        return None
    if code_origin in HELD_ORIGINS:
        return str(code_origin)
    if kind in history_kinds():
        if is_test is True:
            return "test_history"
        return "history" if hold_history else None
    return None


def gates_on(layer: GateLayer) -> tuple[LanguageGate, ...]:
    """The gates holding *layer* back, read from :data:`LANGUAGE_GATES` at call
    time. Preview rows are served, so they are not among them."""
    return tuple(g for g in LANGUAGE_GATES if g.layer == layer and g.state == "gated")


def previews_on(layer: GateLayer) -> tuple[LanguageGate, ...]:
    """The preview rows on *layer*: served by default, labelled."""
    return tuple(g for g in LANGUAGE_GATES if g.layer == layer and g.state == "preview")


@lru_cache(maxsize=1 << 16)
def _suffix(path: str) -> str:
    # Cached: a gate pass asks once per finding, so a file's path repeats.
    return PurePosixPath(path).suffix.lower()


def language_of(path: str) -> str | None:
    """The language tag a stored path's extension maps to, or ``None``."""
    return EXTENSION_TO_LANGUAGE.get(_suffix(path or ""))


def _match(
    gates: Iterable[LanguageGate], path: str, kind: str | None
) -> LanguageGate | None:
    language = language_of(path)
    if language is None:
        return None
    for gate in gates:
        if gate.language == language and (gate.kinds is None or kind in gate.kinds):
            return gate
    return None


def gate_for(layer: GateLayer, path: str, kind: str | None = None) -> LanguageGate | None:
    """The gate keeping a *layer* row on *path* (of *kind*) off default surfaces."""
    return _match(gates_on(layer), path, kind)


def preview_for(layer: GateLayer, path: str, kind: str | None = None) -> LanguageGate | None:
    """The preview row a served *layer* row on *path* (of *kind*) is labelled by."""
    return _match(previews_on(layer), path, kind)


def preview_note(gate: LanguageGate) -> str:
    """The one-line label a preview row's findings carry."""
    if gate.precision is None:
        return f"preview, not measured: {gate.corpus}, {gate.measured_on}"
    return (
        f"preview, measured {round(gate.precision * 100)}% {gate.measure} "
        f"on {gate.corpus}, {gate.measured_on}"
    )


def preview_label(layer: GateLayer, path: str, kind: str | None = None) -> str | None:
    """:func:`preview_note` for a served *layer* row on *path*, else ``None``."""
    gate = preview_for(layer, path, kind)
    return preview_note(gate) if gate is not None else None


def label_previews(layer: GateLayer, rows: Iterable[dict], *, kind: str) -> list[dict]:
    """*rows* (dicts with ``file_path``), each on a preview language copied
    with ``preview`` set to its note; the others unchanged."""
    out: list[dict] = []
    for row in rows:
        note = preview_label(layer, row.get("file_path") or "", row.get(kind))
        out.append({**row, "preview": note} if note else row)
    return out


def served_previews(
    layer: GateLayer,
    rows: Iterable[Any],
    *,
    kind: str,
    get: Callable[[Any, str], Any] = getattr,
) -> dict[str, str]:
    """``{language: preview_note}`` for each preview language among served
    *rows*, so a response labels only the cells it actually lists."""
    out: dict[str, str] = {}
    for row in rows:
        gate = preview_for(layer, get(row, "file_path") or "", get(row, kind))
        if gate is not None:
            out.setdefault(gate.language, preview_note(gate))
    return out


def is_served(
    layer: GateLayer, finding_type: Any, path: str, excluded: frozenset[str]
) -> bool:
    """A row of *finding_type* on *path* reaches a default surface: its type is
    not in *excluded* (an :func:`excluded_types` result) and no gate holds back
    *layer* on its language."""
    return finding_type not in excluded and gate_for(layer, path, finding_type) is None


_LAYER_LABEL: dict[str, str] = {
    "health": "Health",
    "refactoring": "Refactoring",
    "performance": "Performance",
    "dead_code": "Dead code",
}


def preview_lines(
    layer: GateLayer,
    rows: Iterable[Any],
    *,
    kind: str,
    get: Callable[[Any, str], Any] = getattr,
) -> list[str]:
    """One ``"<Layer> on <Language>: <note>"`` line per preview language among
    the listed *rows*, for a terminal report to print under its list."""
    return [
        f"{_LAYER_LABEL[layer]} on {language_label(language)}: {note}"
        for language, note in served_previews(layer, rows, kind=kind, get=get).items()
    ]


def gated_extensions(language: str) -> tuple[str, ...]:
    """Every extension mapped to *language*, lower-case with the dot."""
    return tuple(sorted(ext for ext, tag in EXTENSION_TO_LANGUAGE.items() if tag == language))


def language_label(language: str) -> str:
    """The display name of a language tag (``csharp`` reads ``C#``)."""
    from repowise.core.ingestion.languages import REGISTRY

    spec = REGISTRY.get(language)
    return spec.display_name if spec is not None else language


def held_back_note(languages: Iterable[str]) -> str:
    """The served sentence for rows held back on *languages*."""
    names = [language_label(language) for language in languages]
    return f"Held back on {', '.join(names)} files pending verification."


def gated_summary(layer: GateLayer, counts: Mapping[str, int]) -> dict[str, dict]:
    """``{language: {count, reason}}`` for each gated language with rows on
    *layer*, so a surface says what it held back. The reason says only that
    the layer is held back pending verification there. A :data:`FILE_HOLDS`
    key in *counts* is reported under that key with its reason."""
    out: dict[str, dict] = {}
    for gate in gates_on(layer):
        count = counts.get(gate.language, 0)
        if count:
            out[gate.language] = {"count": count, "reason": held_back_note([gate.language])}
    # File holds ride beside the languages under their own keys, with their reason.
    for key, reason in FILE_HOLDS.items():
        if counts.get(key):
            out[key] = {"count": counts[key], "reason": reason}
    return out


def row_hold(
    layer: GateLayer,
    row: Any,
    files: Mapping[str, Any] | None,
    *,
    kind: str,
    get: Callable[[Any, str], Any] = getattr,
    hold_history: bool = True,
) -> str | None:
    """The :data:`FILE_HOLDS` key on *row*, read off its file's metric row in
    *files* (``code_origin``, ``is_test``); a path missing from it is not held."""
    metric = (files or {}).get(get(row, "file_path") or "")
    if metric is None:
        return None
    return file_hold(
        layer,
        get(row, kind),
        getattr(metric, "code_origin", None),
        getattr(metric, "is_test", None),
        hold_history=hold_history,
    )


def split_held(
    layer: GateLayer,
    rows: Iterable[Any],
    files: Mapping[str, Any] | None,
    *,
    kind: str,
    get: Callable[[Any, str], Any] = getattr,
) -> tuple[list[Any], dict[str, int]]:
    """``(rows no file hold keeps back, {hold key: count})`` for rows already in
    memory whose language gates were applied when they were read."""
    kept: list[Any] = []
    counts: dict[str, int] = {}
    for row in rows:
        key = row_hold(layer, row, files, kind=kind, get=get)
        if key is None:
            kept.append(row)
        else:
            counts[key] = counts.get(key, 0) + 1
    return kept, counts


def split_gated(
    layer: GateLayer,
    rows: Iterable[Any],
    *,
    kind: str,
    get: Callable[[Any, str], Any] = getattr,
    include_unverified: bool = False,
    files: Mapping[str, Any] | None = None,
    hold_history: bool = True,
) -> tuple[list[Any], dict[str, dict]]:
    """``(rows no gate holds back, gated_summary)`` for rows already in memory.

    *kind* names the row field holding the finding type; *get* reads a field.
    *files* maps a path to its health metric row so the :data:`FILE_HOLDS`
    apply too (:func:`row_hold`). ``include_unverified`` keeps every row and
    reports nothing held back; ``hold_history=False`` keeps history markers on
    production files.
    """
    if include_unverified:
        return list(rows), {}
    shown: list[Any] = []
    counts: dict[str, int] = {}
    for row in rows:
        held = row_hold(layer, row, files, kind=kind, get=get, hold_history=hold_history)
        gate = None if held else gate_for(layer, get(row, "file_path") or "", get(row, kind))
        key = held or (gate.language if gate is not None else None)
        if key is None:
            shown.append(row)
        else:
            counts[key] = counts.get(key, 0) + 1
    return shown, gated_summary(layer, counts)


def status_of(finding_type: str) -> FindingTypeStatus:
    return REGISTRY.get(finding_type, _VALIDATED)


def excluded_types(
    *, requested: Iterable[str] = (), include_provisional: bool = False
) -> frozenset[str]:
    """Types a surface must leave out: every hidden type, plus each provisional
    type the caller neither named in *requested* nor opted into."""
    named = set(requested)
    return frozenset(
        name
        for name, entry in REGISTRY.items()
        if entry.status == "hidden"
        or (entry.status == "provisional" and not include_provisional and name not in named)
    )


def is_shown(
    finding_type: str, *, requested: Iterable[str] = (), include_provisional: bool = False
) -> bool:
    return finding_type not in excluded_types(
        requested=requested, include_provisional=include_provisional
    )


def verification_label(finding_type: str) -> str | None:
    """:data:`UNVERIFIED_LABEL` for a provisional type, else ``None``."""
    return UNVERIFIED_LABEL if status_of(finding_type).status == "provisional" else None


def withheld_summary(counts: Mapping[str, int], excluded: Iterable[str]) -> dict[str, dict]:
    """``{type: {count, status, reason}}`` for each excluded type that has rows,
    so a surface can say what it held back instead of silently shrinking."""
    out: dict[str, dict] = {}
    for name in sorted(set(excluded)):
        count = counts.get(name, 0)
        if count:
            entry = status_of(name)
            out[name] = {"count": count, "status": entry.status, "reason": entry.reason}
    return out


__all__ = [
    "FILE_HOLDS",
    "HELD_ORIGINS",
    "LANGUAGE_GATES",
    "MAP_HELD_ORIGINS",
    "REGISTRY",
    "UNVERIFIED_LABEL",
    "FindingStatus",
    "FindingTypeStatus",
    "GateLayer",
    "GateState",
    "LanguageGate",
    "excluded_types",
    "file_hold",
    "gate_for",
    "gated_extensions",
    "gated_summary",
    "gates_on",
    "history_kinds",
    "is_served",
    "is_shown",
    "label_previews",
    "language_of",
    "map_held",
    "preview_for",
    "preview_label",
    "preview_lines",
    "preview_note",
    "previews_on",
    "row_hold",
    "served_previews",
    "split_gated",
    "split_held",
    "status_of",
    "stored_origin",
    "verification_label",
    "withheld_summary",
]

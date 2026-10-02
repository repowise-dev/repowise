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
ungating it is deleting its row here after a re-audit clears it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
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
            "Private-symbol findings were almost all false: TS/JS/Python emit no "
            "'reads' edges, so same-file uses are invisible to the graph."
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


@dataclass(frozen=True)
class LanguageGate:
    layer: GateLayer
    #: An ``EXTENSION_TO_LANGUAGE`` tag; a file's extension decides its language.
    language: str
    #: Measured precision on the sample that failed the bar.
    precision: float
    #: What the measurement ran on, in words (no repository names).
    corpus: str
    #: ISO date of the measurement.
    measured_on: str
    reason: str
    #: The kinds gated, or ``None`` for every kind of the layer.
    kinds: frozenset[str] | None = None


_AUDIT_DATE = "2026-10-02"
_JAVA = "hand-judged sample of a 34k-file Java search engine"
_DOTNET = "hand-judged samples of a 59k-file C#/C/C++ runtime and an 8k-file C#/C++ suite"
_NO_PLAN = "No judged plan was acceptable as written."
_NOT_A_COST = "Most judged opportunities were not real costs."

# Every layer measured below 90% on every audited language, so whole layers
# are gated (``kinds=None``). Precision is the lowest share of true positives
# in a judged sample (debatable counted as a miss). Columns: layer, language,
# precision, corpus, reason.
_GATE_ROWS: tuple[tuple[GateLayer, str, float, str, str], ...] = (
    ("health", "java", 0.09, _JAVA, "Field reads without `this.` and Java idioms read as defects."),
    ("health", "csharp", 0.06, _DOTNET, "Implicit member access and stubs read as defects."),
    ("health", "c", 0.09, _DOTNET, "Vendored and generated C read as defects."),
    ("health", "cpp", 0.09, _DOTNET, "Implicit member access and vendored code read as defects."),
    ("refactoring", "java", 0.0, _JAVA, _NO_PLAN),
    ("refactoring", "csharp", 0.09, _DOTNET, _NO_PLAN),
    ("refactoring", "c", 0.0, _DOTNET, _NO_PLAN),
    ("refactoring", "cpp", 0.09, _DOTNET, _NO_PLAN),
    ("performance", "java", 0.32, _JAVA, "In-memory lookups are read as database calls."),
    ("performance", "csharp", 0.06, _DOTNET, _NOT_A_COST),
    ("performance", "c", 0.06, _DOTNET, _NOT_A_COST),
    ("performance", "cpp", 0.0, _DOTNET, _NOT_A_COST),
    ("dead_code", "java", 0.20, _JAVA, "Reflection and plugin entry points are unseen."),
    ("dead_code", "csharp", 0.07, _DOTNET, "A `using` reaches one file of its namespace."),
    ("dead_code", "c", 0.04, _DOTNET, "P/Invoke, call-table and typedef uses are unseen."),
    ("dead_code", "cpp", 0.04, _DOTNET, "Uses through DLL tables, COM and callbacks are unseen."),
)

LANGUAGE_GATES: tuple[LanguageGate, ...] = tuple(
    LanguageGate(layer, language, precision, corpus, _AUDIT_DATE, reason)
    for layer, language, precision, corpus, reason in _GATE_ROWS
)


def gates_on(layer: GateLayer) -> tuple[LanguageGate, ...]:
    """The gates on *layer*, read from :data:`LANGUAGE_GATES` at call time."""
    return tuple(gate for gate in LANGUAGE_GATES if gate.layer == layer)


def language_of(path: str) -> str | None:
    """The language tag a stored path's extension maps to, or ``None``."""
    return EXTENSION_TO_LANGUAGE.get(PurePosixPath(path or "").suffix.lower())


def gate_for(layer: GateLayer, path: str, kind: str | None = None) -> LanguageGate | None:
    """The gate keeping a *layer* row on *path* (of *kind*) off default surfaces."""
    language = language_of(path)
    if language is None:
        return None
    for gate in gates_on(layer):
        if gate.language == language and (gate.kinds is None or kind in gate.kinds):
            return gate
    return None


def is_served(
    layer: GateLayer, finding_type: Any, path: str, excluded: frozenset[str]
) -> bool:
    """A row of *finding_type* on *path* reaches a default surface: its type is
    not in *excluded* (an :func:`excluded_types` result) and no gate holds back
    *layer* on its language."""
    return finding_type not in excluded and gate_for(layer, path, finding_type) is None


def gated_extensions(language: str) -> tuple[str, ...]:
    """Every extension mapped to *language*, lower-case with the dot."""
    return tuple(sorted(ext for ext, tag in EXTENSION_TO_LANGUAGE.items() if tag == language))


def gated_summary(layer: GateLayer, counts: Mapping[str, int]) -> dict[str, dict]:
    """``{language: {count, precision, reason}}`` for each gated language with
    rows on *layer*, so a surface says what it held back and why."""
    out: dict[str, dict] = {}
    for gate in gates_on(layer):
        count = counts.get(gate.language, 0)
        if count:
            out[gate.language] = {
                "count": count,
                "precision": gate.precision,
                "reason": gate.reason,
            }
    return out


def split_gated(
    layer: GateLayer,
    rows: Iterable[Any],
    *,
    kind: str,
    get: Callable[[Any, str], Any] = getattr,
    include_unverified: bool = False,
) -> tuple[list[Any], dict[str, dict]]:
    """``(rows no gate holds back, gated_summary)`` for rows already in memory.

    *kind* names the row field holding the finding type; *get* reads a field.
    ``include_unverified`` keeps every row and reports nothing held back.
    """
    if include_unverified:
        return list(rows), {}
    shown: list[Any] = []
    counts: dict[str, int] = {}
    for row in rows:
        gate = gate_for(layer, get(row, "file_path") or "", get(row, kind))
        if gate is None:
            shown.append(row)
        else:
            counts[gate.language] = counts.get(gate.language, 0) + 1
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
    "LANGUAGE_GATES",
    "REGISTRY",
    "UNVERIFIED_LABEL",
    "FindingStatus",
    "FindingTypeStatus",
    "GateLayer",
    "LanguageGate",
    "excluded_types",
    "gate_for",
    "gated_extensions",
    "gated_summary",
    "gates_on",
    "is_served",
    "is_shown",
    "language_of",
    "split_gated",
    "status_of",
    "verification_label",
    "withheld_summary",
]

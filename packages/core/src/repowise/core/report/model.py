"""The report document, its evidence items and the ``report-section`` seam.

Each evidence item ("check") keeps the keys of an OpenSSF Scorecard v2 check
result (``name``, ``score``, ``reason``, ``details``, ``documentation`` with
``url`` and ``short``) and adds ``method`` and ``basis``. As in Scorecard,
``score`` is 0 to 10 and -1 means inconclusive. Unlike Scorecard it may be a
one-decimal float, and it is ``None`` on a descriptive item (counts and lists
with no scale): evidence is never turned into a score it does not carry.

A section is registered under ``SeamRegistry("report-section")`` by name; a
plugin adds its own the same way, building its items with :func:`check`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.registry.plugins import SeamRegistry

if TYPE_CHECKING:
    from repowise.core.changes.period import PeriodRequest

SCHEMA_VERSION = 1
INCONCLUSIVE = -1
#: ``measured`` (counted from the index), ``inferred`` (derived by a static
#: rule), ``declared`` (read from what the repository says about itself),
#: ``unresolved`` (the evidence is missing) and ``gated`` (held back on a
#: language the finding layer is not verified for).
BASES = ("measured", "inferred", "declared", "unresolved", "gated")
DOCS_URL = (
    "https://github.com/repowise-dev/repowise/blob/main/docs/reference/CLI_REFERENCE.md"
    "#repowise-report-assessment-path"
)


def check(
    name: str,
    *,
    reason: str,
    basis: str,
    method: str,
    score: float | None = None,
    details: Iterable[str] = (),
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One evidence item. Missing or held-back evidence is always inconclusive."""
    if basis not in BASES:
        raise ValueError(f"unknown basis {basis!r}; expected one of {BASES}")
    if basis in ("unresolved", "gated"):
        score = INCONCLUSIVE
    elif score is not None:
        score = round(min(max(float(score), 0.0), 10.0), 1)
    item: dict[str, Any] = {
        "name": name,
        "score": score,
        "reason": reason,
        "details": list(details),
        "method": method,
        "basis": basis,
    }
    if data is not None:
        item["data"] = data
    return item


#: Detail lines one item lists before it says how many more there are.
MAX_DETAILS = 20


def capped(lines: list[str], limit: int = MAX_DETAILS) -> list[str]:
    """*lines*, cut at *limit* with a closing count of the rest."""
    if len(lines) <= limit:
        return lines
    return [*lines[:limit], f"and {len(lines) - limit} more"]


#: Component names one summary line lists before it says how many more there are.
MAX_NAMES = 30


def capped_names(names: list[str]) -> str:
    """*names* joined, cut at :data:`MAX_NAMES` with a count of the rest."""
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (f" and {len(names) - MAX_NAMES} more" if len(names) > MAX_NAMES else "")


def component_summary(
    name: str,
    *,
    outcomes: dict[str, int],
    quiet: list[str],
    method: str,
    basis: str,
    data: dict[str, Any],
    named: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """The one summary row a per-component section opens with.

    Components get an item of their own only when there is something to say;
    the rest are named here, under a label in *named* or as *quiet*, and *data*
    keeps every component's figures, so the JSON loses nothing the shorter
    text leaves out.
    """
    counted = ", ".join(f"{label}: {n}" for label, n in outcomes.items() if n)
    details = [
        f"{label}: {capped_names(names)}" for label, names in (named or {}).items() if names
    ]
    if quiet:
        details.append(f"nothing to note: {capped_names(quiet)}")
    return check(
        name,
        reason=f"{sum(outcomes.values())} component(s); {counted or 'none'}",
        details=details,
        method=method,
        basis=basis,
        data=data,
    )


@dataclass
class ReportContext:
    """One indexed repository, open for reading, with shared reads cached."""

    session: Any
    repository_id: str
    repo_root: Path
    alias: str | None = None
    head_commit: str | None = None
    #: The period a ``changes`` report covers and how it builds missing
    #: checklists (a :class:`~repowise.core.changes.period.PeriodRequest`);
    #: ``None`` is the default request: the 90 days before the newest
    #: indexed commit, building what is missing.
    period: PeriodRequest | None = None
    #: ``lookup`` and ``alias`` for the dead-code cross-repo cut
    #: (``dead_code.serving.serve_findings``) when the repository is a workspace
    #: member; ``None`` reads stored values.
    cross_repo: dict[str, Any] | None = None
    _cache: dict[str, Any] = field(default_factory=dict)

    async def components(self) -> Any:
        """Declared and derived components (:class:`RepoComponents`)."""
        if "components" not in self._cache:
            from repowise.core.components.store import load_repo_components, workspace_services

            self._cache["components"] = await load_repo_components(
                self.session,
                self.repository_id,
                self.repo_root,
                services=workspace_services(self.repo_root),
            )
        return self._cache["components"]

    async def rollups(self) -> dict[str, dict[str, Any]]:
        """Component rollups by name, plus the whole repository under ``None``."""
        if "rollups" not in self._cache:
            from repowise.core.components.store import component_rollups

            found = await self.components()
            files = {n: p for n, p in found.resolved.files.items() if p}
            everything = sorted(
                {f for paths in files.values() for f in paths} | set(found.resolved.unassigned)
            )
            rolled: dict[Any, dict[str, Any]] = await component_rollups(
                self.session, self.repository_id, files, found.test_files
            )
            if everything:
                whole = await component_rollups(
                    self.session, self.repository_id, {"": everything}, found.test_files
                )
                rolled[None] = whole[""]
            self._cache["rollups"] = rolled
        return self._cache["rollups"]


@dataclass
class WorkspaceReportContext:
    """A workspace's stored cross-repo artifacts, loaded once and shared by its sections.

    Reads only what ``repowise update --workspace`` wrote under
    ``.repowise-workspace/``; nothing is extracted or matched again here.
    """

    ws_root: Path
    #: The document's ``repos`` entries, one per configured repository
    #: (``head_commit`` when indexed, ``indexed: False`` otherwise).
    repos: list[dict[str, Any]] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict)

    def _load(self, key: str, loader: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = loader()
        return self._cache[key]

    def config(self) -> Any:
        """The workspace config (:class:`~repowise.core.workspace.config.WorkspaceConfig`)."""
        from repowise.core.workspace.config import WorkspaceConfig

        return self._load("config", lambda: WorkspaceConfig.load(self.ws_root))

    def system_graph(self) -> Any:
        """The stored system graph, or ``None`` when none was built."""
        from repowise.core.workspace.system_graph import load_system_graph

        return self._load("graph", lambda: load_system_graph(self.ws_root))

    def contract_store(self) -> Any:
        """The stored contract store, or ``None`` when none was extracted."""
        from repowise.core.workspace.contracts import load_contract_store

        return self._load("contracts", lambda: load_contract_store(self.ws_root))

    def evidence(self) -> Any:
        """The graph's evidence state (:class:`GraphEvidence`), ``None`` without a graph."""
        from repowise.core.workspace.graph_evidence import graph_evidence

        graph = self.system_graph()
        return self._load("evidence", lambda: graph_evidence(graph) if graph else None)

    def repo_health(self) -> dict[str, dict[str, Any]] | None:
        """Each member's stored health headline from ``workspace.db``, ``None`` without one."""
        from repowise.core.workspace.store import read_repo_health, store_path

        return self._load("health", lambda: read_repo_health(store_path(self.ws_root)))

    async def each_index(
        self, key: str, read: Callable[[Any, str], Awaitable[Any]]
    ) -> dict[str, Any]:
        """``alias -> await read(session, repository_id)`` over every member with an
        index, cached under *key*. A member whose index cannot be opened or read
        maps to the exception instead, so a section can say which one it missed.
        """
        if key in self._cache:
            return self._cache[key]
        from repowise.core.workspace.registry import open_repo_db, repo_db_path
        from repowise.core.workspace.repo_index import checkout_repository

        out: dict[str, Any] = {}
        for entry in self.config().repos:
            root = (self.ws_root / entry.path).resolve()
            if not repo_db_path(root).is_file():
                continue
            try:
                engine, sessions = await open_repo_db(root)
            except Exception as exc:  # one unreadable index must not lose the rest
                out[entry.alias] = exc
                continue
            try:
                async with sessions() as session:
                    repo = await checkout_repository(session, root)
                    out[entry.alias] = (
                        await read(session, repo.id)
                        if repo is not None
                        else LookupError("the index holds no repository row")
                    )
            except Exception as exc:
                out[entry.alias] = exc
            finally:
                await engine.dispose()
        self._cache[key] = out
        return out


#: Where a section runs: once per repository, or once per workspace.
SCOPES = ("repo", "workspace")


@dataclass(frozen=True)
class Section:
    """A report section: *collect* returns its checks, or ``None`` when the
    section does not apply (it is then left out).

    A ``repo`` section gets a :class:`ReportContext` per repository. A
    ``workspace`` section gets one :class:`WorkspaceReportContext` and runs
    once, only when the report covers a whole workspace.
    """

    title: str
    #: One line saying what the section measures; the documentation ``short``.
    summary: str
    collect: Callable[[Any], Awaitable[list[dict[str, Any]] | None]]
    #: Sections are listed by this, then by name.
    order: int = 100
    #: The report kind the section belongs to (``assessment``, ``changes``, ``estate``).
    report: str = "assessment"
    #: One of :data:`SCOPES`.
    scope: str = "repo"

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"unknown section scope {self.scope!r}; expected one of {SCOPES}")


report_sections: SeamRegistry[Section] = SeamRegistry("report-section")

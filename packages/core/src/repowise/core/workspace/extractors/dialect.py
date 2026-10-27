"""The dialect protocol every contract type shares, and the extractor that runs it.

A *dialect* is one framework's, library's or file format's view of a source
file: it names the extensions it reads and turns a file into contracts. Each
contract type is a package of dialects plus a registry tuple, and every
dialect runs through :func:`run_dialect`, so adding a library is one module
and one registry entry.

The engine, not the dialect, owns the two checks every dialect needs. A
dialect declares ``requires``, the tokens one of which a file must contain
before it is read at all; the file's :class:`..base.ScanContext` looks each
token up once, however many dialects name it. And a contract whose line is
nothing but a comment is dropped, so a usage example in a doc comment never
becomes an endpoint, a queue or a table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from repowise.core.workspace.origins import ContractOrigin, origin_confidence

from .base import ScanContext, select_files

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Sequence
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract

    from .base import SourceFile
    from .calls import CallShape


class ContractDialect(Protocol):
    """A recogniser for the files whose extension is in ``extensions``."""

    name: str
    extensions: frozenset[str]
    #: Tokens one of which a file must contain to be read (a library's import,
    #: a decorator's name): the cheap test that keeps a dialect off the files
    #: it can find nothing in. Every token any match needs, so the gate never
    #: drops a contract. :data:`EVERY_FILE` for a contract file format.
    requires: tuple[str, ...]

    def extract(self, ctx: ScanContext) -> list[Contract]:
        """Return the contracts found in *ctx* (may be empty)."""
        ...


#: The gate of a dialect that reads a contract format (``.proto``, ``.sql``,
#: ``schema.prisma``), where the extension already says the file is one.
EVERY_FILE: tuple[str, ...] = ("",)


def rows_gate(rows: Iterable[CallShape]) -> tuple[str, ...]:
    """A table dialect's gate: every token its rows require, or ``()`` if a row requires none.

    An ungated row leaves the whole table ungated, which the registry test refuses.
    """
    tokens: dict[str, None] = {}
    for row in rows:
        if not row.requires:
            return ()
        tokens.update(dict.fromkeys(row.requires))
    return tuple(tokens)


def runs_on(dialect: ContractDialect, ctx: ScanContext) -> bool:
    """Whether *dialect* reads *ctx*'s file: its extension, then its gate."""
    return ctx.suffix in dialect.extensions and ctx.has_any(dialect.requires)


def without_comments(contracts: list[Contract], ctx: ScanContext) -> list[Contract]:
    """*contracts* less those whose line in *ctx*'s file is only a comment."""
    if not contracts:
        return contracts
    return [c for c in contracts if not ctx.comment_only_line(c.line)]


def run_dialect(dialect: ContractDialect, ctx: ScanContext) -> list[Contract]:
    """The contracts *dialect* reads in *ctx*, gated and with commented-out matches dropped."""
    if not runs_on(dialect, ctx):
        return []
    return without_comments(dialect.extract(ctx), ctx)


class RepoPass(Protocol):
    """One extraction run's state for a dialect whose contracts span files.

    A dialect with a ``repo_pass()`` factory is handed each file through
    :meth:`scan` instead of ``extract``, and names its contracts in
    :meth:`finish` once the whole repo has been read: a Laravel job
    dispatched in a controller runs on the queue its own class declares.
    A fresh pass per run keeps repos extracted concurrently apart.
    """

    def scan(self, ctx: ScanContext) -> None: ...

    def finish(self) -> list[Contract]: ...


def build_contract(
    ctx: ScanContext,
    *,
    contract_type: str,
    contract_id: str,
    role: str,
    symbol_name: str,
    origin: ContractOrigin,
    line: int | None,
    meta: dict,
) -> Contract:
    """A :class:`Contract` read from *ctx*'s file. The service is assigned later.

    A dialect declares *origin*, what established the contract, from the closed
    vocabulary in :mod:`..origins`; the confidence is the origin's, never a
    number of the dialect's own.
    """
    from repowise.core.workspace.contracts import Contract

    return Contract(
        repo=ctx.repo_alias,
        contract_id=contract_id,
        contract_type=contract_type,
        role=role,
        file_path=ctx.rel_path,
        symbol_name=symbol_name,
        confidence=origin_confidence(origin),
        service=None,
        line=line,
        meta=meta,
        origin=origin,
    )


def union_extensions(dialects: Iterable[ContractDialect]) -> frozenset[str]:
    """Every extension any of *dialects* reads."""
    out: set[str] = set()
    for d in dialects:
        out |= d.extensions
    return frozenset(out)


def file_identity(c: Contract) -> Hashable:
    """One fact per file: a contract id read once or ten times in a file is one contract."""
    return (c.file_path, c.contract_id, c.role)


def dedupe(contracts: list[Contract], identity: Callable[[Contract], Hashable]) -> list[Contract]:
    """The first contract per *identity*, in order."""
    seen: set[Hashable] = set()
    out: list[Contract] = []
    for c in contracts:
        key = identity(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


class DialectExtractor:
    """Walk a repo's files once and hand each to the dialects that read it.

    Subclasses set ``dialects``. ``identity``, when set, keeps the first
    contract per key within a file, for contract types whose many call sites
    of one name are one fact. ``unresolved_stat``, when set, is the *stats*
    key that counts the calls a dialect located but could not name
    (:meth:`..base.ScanContext.note_unresolved`).
    """

    dialects: tuple[ContractDialect, ...] = ()
    identity: Callable[[Contract], Hashable] | None = None
    unresolved_stat: str | None = None

    @classmethod
    def source_extensions(cls) -> frozenset[str]:
        """Every extension this extractor's dialects claim."""
        return union_extensions(cls.dialects)

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
        files: Sequence[SourceFile] | None = None,
        stats: dict[str, int] | None = None,
    ) -> list[Contract]:
        """Scan *repo_path* (or the already-walked *files*) for contracts.

        *stats* is an optional out-dict that ``unresolved_stat`` is added to.
        """
        contracts: list[Contract] = []
        unresolved = 0
        passes: dict[int, RepoPass] = {
            id(d): d.repo_pass() for d in self.dialects if hasattr(d, "repo_pass")
        }
        passed: dict[str, ScanContext] = {}  # files a repo pass read, for its comment check
        for rel_path, suffix, content in select_files(
            repo_path, self.source_extensions(), exclude, files
        ):
            ctx = ScanContext(repo_alias, rel_path, suffix, content)
            found: list[Contract] = []
            for dialect in self.dialects:
                repo_pass = passes.get(id(dialect))
                if repo_pass is None:
                    found.extend(run_dialect(dialect, ctx))
                elif runs_on(dialect, ctx):
                    repo_pass.scan(ctx)
                    passed[rel_path] = ctx
            contracts.extend(found if self.identity is None else dedupe(found, self.identity))
            unresolved += ctx.unresolved_calls
        if stats is not None and self.unresolved_stat and unresolved:
            stats[self.unresolved_stat] = stats.get(self.unresolved_stat, 0) + unresolved
        for repo_pass in passes.values():
            found = [
                c for c in repo_pass.finish()
                if c.file_path not in passed or not passed[c.file_path].comment_only_line(c.line)
            ]
            # Every identity names the file, so a whole-repo dedupe is per file.
            contracts.extend(found if self.identity is None else dedupe(found, self.identity))
        return contracts


__all__ = [
    "EVERY_FILE",
    "ContractDialect",
    "DialectExtractor",
    "RepoPass",
    "build_contract",
    "dedupe",
    "file_identity",
    "rows_gate",
    "run_dialect",
    "runs_on",
    "union_extensions",
    "without_comments",
]

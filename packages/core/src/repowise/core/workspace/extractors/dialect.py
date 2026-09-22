"""The dialect protocol every contract type shares, and the extractor that runs it.

A *dialect* is one framework's, library's or file format's view of a source
file: it names the extensions it reads and turns a file into contracts. Each
contract type is a package of dialects plus a registry tuple, and
:class:`DialectExtractor` owns the only walk-and-dispatch loop, so adding a
library is one module and one registry entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .base import ScanContext, select_files

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Sequence
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract

    from .base import SourceFile


class ContractDialect(Protocol):
    """A recogniser for the files whose extension is in ``extensions``."""

    name: str
    extensions: frozenset[str]

    def extract(self, ctx: ScanContext) -> list[Contract]:
        """Return the contracts found in *ctx* (may be empty)."""
        ...


def build_contract(
    ctx: ScanContext,
    *,
    contract_type: str,
    contract_id: str,
    role: str,
    symbol_name: str,
    confidence: float,
    line: int | None,
    meta: dict,
) -> Contract:
    """A :class:`Contract` read from *ctx*'s file. The service is assigned later."""
    from repowise.core.workspace.contracts import Contract

    return Contract(
        repo=ctx.repo_alias,
        contract_id=contract_id,
        contract_type=contract_type,
        role=role,
        file_path=ctx.rel_path,
        symbol_name=symbol_name,
        confidence=confidence,
        service=None,
        line=line,
        meta=meta,
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
    of one name are one fact.
    """

    dialects: tuple[ContractDialect, ...] = ()
    identity: Callable[[Contract], Hashable] | None = None

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
    ) -> list[Contract]:
        """Scan *repo_path* (or the already-walked *files*) for contracts."""
        contracts: list[Contract] = []
        for rel_path, suffix, content in select_files(
            repo_path, self.source_extensions(), exclude, files
        ):
            ctx = ScanContext(repo_alias, rel_path, suffix, content)
            found: list[Contract] = []
            for dialect in self.dialects:
                if suffix in dialect.extensions:
                    found.extend(dialect.extract(ctx))
            contracts.extend(found if self.identity is None else dedupe(found, self.identity))
        return contracts


__all__ = [
    "ContractDialect",
    "DialectExtractor",
    "build_contract",
    "dedupe",
    "file_identity",
    "union_extensions",
]

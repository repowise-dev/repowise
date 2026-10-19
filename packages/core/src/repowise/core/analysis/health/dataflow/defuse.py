"""Per-block def/use aggregation over a function's CFG.

This module is language-agnostic: it walks the CFG's blocks and statements and
delegates the read-vs-write classification of each statement to the language's
:class:`DefUseDialect`. The output -- a :class:`Definition` for every write site
plus the variable reads per block -- is exactly the input the reaching-
definitions fixpoint (:mod:`reaching`) consumes. Adding a language is a new
dialect; this orchestration never changes.

A :class:`Definition` is a single write site identified by a stable, ascending
``index`` (assigned in block-id then statement order) so def sets can be
represented as integer sets for a fast, deterministic fixpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .dialects.base import Captured, Occurrence

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .cfg import CFG
    from .dialects.base import BaseDefUseDialect, StatementDefUse


@dataclass(frozen=True)
class Definition:
    """One write site: ``var`` assigned at ``line`` inside block ``block_id``.

    ``index`` is a stable per-function ordinal (the def's identity in the
    reaching-definitions fixpoint). Parameters are definitions too, attributed
    to the CFG entry block.
    """

    var: str
    block_id: int
    index: int
    line: int  # 1-indexed
    #: Where on ``line`` the written name starts (0-indexed), so the write's
    #: node can be found again in the retained tree.
    column: int = 0
    #: Where on ``line`` a declared name starts to exist; see ``Occurrence``.
    declared_at: int | None = None
    #: The write declares the name (``Occurrence.declares``).
    declares: bool = False
    #: An import binds the name (``Occurrence.imports``).
    imports: bool = False
    #: The write may not happen (``Occurrence.may``).
    may: bool = False
    #: The declaration gives the name no value (``Occurrence.valueless``).
    valueless: bool = False


@dataclass
class BlockDefUse:
    """The write sites and variable reads within a single basic block."""

    block_id: int
    defs: list[Definition] = field(default_factory=list)
    uses: list[Occurrence] = field(default_factory=list)


@dataclass
class FunctionDefUse:
    """Def/use facts for a whole function.

    ``blocks`` is keyed by block id; ``definitions`` is every write site ordered
    by ``index`` (so ``definitions[i].index == i``); ``params`` is the parameter
    occurrences seeded at the entry block. ``outer`` is, per name the
    function declares ``global`` / ``nonlocal``, the declaring lines.
    ``captured`` is what nested
    closures read and write of this function's variables (see
    :class:`Captured`), kept apart from the per-block uses because a closure
    runs when it is called, not where it is written; code that moves
    statements (the Extract Method slicer) still has to count it.
    """

    blocks: dict[int, BlockDefUse]
    definitions: list[Definition]
    params: tuple[Occurrence, ...]
    captured: Captured = field(default_factory=Captured)
    outer: dict[str, frozenset[int]] = field(default_factory=dict)

    def block(self, block_id: int) -> BlockDefUse | None:
        return self.blocks.get(block_id)


def compute_def_use(
    cfg: CFG,
    fn_node: Node,
    lmap: LanguageNodeMap,
    dialect: BaseDefUseDialect,
) -> FunctionDefUse:
    """Build per-block def/use facts for *cfg* using *dialect*.

    Parameters are seeded as definitions at the entry block; each statement's
    writes become :class:`Definition` records with ascending indices, and its
    reads are accumulated per block.
    """
    blocks: dict[int, BlockDefUse] = {}
    definitions: list[Definition] = []
    counter = 0
    outer: dict[str, set[int]] = {}

    def _add_def(occ: Occurrence, block_id: int) -> None:
        nonlocal counter
        bdu = blocks.setdefault(block_id, BlockDefUse(block_id))
        definition = Definition(
            var=occ.name,
            block_id=block_id,
            index=counter,
            line=occ.line,
            column=occ.column,
            declared_at=occ.declared_at,
            declares=occ.declares,
            imports=occ.imports,
            may=occ.may,
            valueless=occ.valueless,
        )
        counter += 1
        bdu.defs.append(definition)
        definitions.append(definition)

    # Parameters reach the body: seed them at the entry block. The dialect is
    # given the whole function node so languages that bind names outside the
    # ``parameters`` field (Go's method ``receiver``) can seed those too.
    params = dialect.parameter_defs(fn_node)
    blocks.setdefault(cfg.entry_id, BlockDefUse(cfg.entry_id))
    for occ in params:
        _add_def(occ, cfg.entry_id)

    for block in cfg.blocks:  # ordered by id => deterministic def indices
        bdu = blocks.setdefault(block.id, BlockDefUse(block.id))
        for stmt in block.statements:
            if stmt.node is None:
                continue
            sdu = _statement_def_use(dialect, stmt.node, lmap, stmt.is_head)
            for occ in sdu.defs:
                _add_def(occ, block.id)
            bdu.uses.extend(sdu.uses)
            for occ in dialect.outer_declared(stmt.node):
                outer.setdefault(occ.name, set()).add(stmt.start_line)

    captured = Captured()
    dialect.collect_captured(fn_node.child_by_field_name("body"), captured, lmap)
    return FunctionDefUse(
        blocks=blocks,
        definitions=definitions,
        params=params,
        captured=captured,
        outer={name: frozenset(lines) for name, lines in outer.items()},
    )


def _statement_def_use(
    dialect: BaseDefUseDialect, node: Node, lmap: LanguageNodeMap, is_head: bool
) -> StatementDefUse:
    """One recorded statement's def/use: an exception handler's head through
    :meth:`~BaseDefUseDialect.handler_def_use`, anything else as a statement."""
    if is_head and node.type in lmap.catch_kinds:
        return dialect.handler_def_use(node)
    return dialect.statement_def_use(node, lmap, head_only=is_head)

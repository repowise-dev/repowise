"""The ``DefUseDialect`` plugin contract + the ``DEFUSE_DIALECTS`` registry.

The dataflow def/use pass is language-agnostic: every grammar difference lives
in a ``DefUseDialect``. This mirrors the per-language plugin idiom the rest of
the pipeline already uses (``perf/dialects/``, ``complexity/languages.py``,
``resolvers/``, ...) -- one module per language, registered in a dict, zero
edits to the core (``defuse.py`` / ``reaching.py``) to add one.

A dialect answers exactly one question, in two shapes:

================================  ============================================
Member                            What it answers
================================  ============================================
``statement_def_use(node, lmap,   the variables a single statement *writes*
``head_only)``                    (defs) and *reads* (uses). ``head_only`` is
                                  set for a compound construct's head (an
                                  ``if`` / ``while`` / ``for`` test) so the
                                  dialect inspects only the condition / loop
                                  clause, not the body (which lives in other
                                  CFG blocks).
``parameter_defs(fn_node)``       the parameter names a function signature
                                  binds -- seeded as defs at the CFG entry. The
                                  whole function node is passed so a language can
                                  also seed names bound outside the
                                  ``parameters`` field (a Go method receiver).
================================  ============================================

:class:`BaseDefUseDialect` carries the language-agnostic machinery a concrete
dialect reuses: identifier-name extraction and a member-access / keyword-aware
*read* collector (so ``obj.attr`` reads ``obj`` but not the member name, and
``f(key=x)`` reads ``x`` but not the keyword ``key``). A new language subclasses
it, declares its member-access and keyword node kinds, and implements the
write-site extraction for its assignment shapes -- typically ~100 lines, and the
def/use core plus the reaching-definitions fixpoint never change.

Every facet defaults to "no signal": a language with no registered dialect
produces no def/use and therefore no reaching definitions, the same
precision-first contract the perf pillar depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap


@dataclass(frozen=True)
class Occurrence:
    """One variable reference at a source line: a def or a use of ``name``."""

    name: str
    line: int  # 1-indexed


@dataclass(frozen=True)
class StatementDefUse:
    """The variables one statement writes (``defs``) and reads (``uses``).

    Order is source order, de-duplication is left to the caller; both are
    deterministic for a given statement so downstream fixpoints are stable.
    """

    defs: tuple[Occurrence, ...]
    uses: tuple[Occurrence, ...]


@runtime_checkable
class DefUseDialect(Protocol):
    """The contract a per-language def/use dialect satisfies."""

    language: str

    def statement_def_use(
        self, node: Node, lmap: LanguageNodeMap, *, head_only: bool
    ) -> StatementDefUse: ...

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]: ...


class BaseDefUseDialect:
    """Shared machinery for concrete dialects.

    Subclasses set :attr:`member_access_kinds` / :attr:`keyword_kinds` (usually
    sourced from the language's ``LanguageNodeMap``) and implement
    :meth:`statement_def_use`. The read collector and identifier helpers here
    are grammar-neutral enough to serve every full-tier language without
    override.
    """

    #: Language tag this dialect serves (informational; the registry is the
    #: source of truth for dispatch).
    language: str = ""

    #: Node types representing ``receiver.member`` access. When collecting
    #: reads, only the receiver is a variable; the member name is skipped.
    member_access_kinds: frozenset[str] = frozenset()

    #: Node types for a keyword / named argument (``f(key=value)``). Only the
    #: value is a variable read; the keyword name is skipped.
    keyword_kinds: frozenset[str] = frozenset()

    #: Identifier leaf node types that name a variable.
    identifier_kinds: frozenset[str] = frozenset({"identifier"})

    # -- identifier helpers ---------------------------------------------------

    def _name(self, node: Node | None) -> str | None:
        if node is None or node.type not in self.identifier_kinds or node.text is None:
            return None
        return node.text.decode("utf-8", "replace")

    def _occ(self, node: Node) -> Occurrence:
        return Occurrence(
            name=(node.text or b"").decode("utf-8", "replace"), line=node.start_point[0] + 1
        )

    # -- read (use) collection ------------------------------------------------

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Append every variable *read* under *node* to *out*, in source order.

        Member names (``obj.attr`` -> only ``obj``) and keyword-argument names
        (``f(key=x)`` -> only ``x``) are not variable reads and are skipped, so
        the use set stays precision-first. Nested function / lambda bodies are
        NOT descended into -- their reads belong to a different scope.
        """
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            out.append(self._occ(node))
            return
        if t in self.member_access_kinds:
            # Only the receiver is a variable read; the member name is not.
            receiver = node.child_by_field_name("object") or node.child_by_field_name("value")
            if receiver is not None:
                self.collect_reads(receiver, out)
            elif node.named_child_count:
                self.collect_reads(node.named_children[0], out)
            return
        if t in self.keyword_kinds:
            self.collect_reads(node.child_by_field_name("value"), out)
            return
        if self._is_scope_boundary(node):
            return
        for child in node.named_children:
            self.collect_reads(child, out)

    def _is_scope_boundary(self, node: Node) -> bool:
        """True if *node* opens a nested scope whose reads are not this
        statement's (a nested function / lambda). Default: never. Subclasses
        override for their function/lambda node kinds."""
        return False

    # -- defaults a subclass may override -------------------------------------

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]:
        """Parameter names bound by *fn_node*'s signature, as entry defs.
        Default: none (a language with no override seeds no parameters)."""
        return ()

    def statement_def_use(
        self, node: Node, lmap: LanguageNodeMap, *, head_only: bool
    ) -> StatementDefUse:  # pragma: no cover - abstract
        raise NotImplementedError


# The registry, populated by ``dialects/__init__.py`` from each language module.
# Keyed by ``LanguageTag``; a missing key => the def/use pass is silent for that
# language (no dialect = no signal).
DEFUSE_DIALECTS: dict[str, BaseDefUseDialect] = {}


def get_defuse_dialect(language: str) -> BaseDefUseDialect | None:
    """Return the def/use dialect for *language*, or ``None`` when unmapped."""
    return DEFUSE_DIALECTS.get(language)

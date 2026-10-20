"""Control-flow graph construction for a single function (Python).

Three stages, all in one structural pass:

1. **Statement sequencer** -- a function body's named children become an
   ordered statement list. Tree-sitter exposes these; nothing materialises
   them today.
2. **Basic-block splitter** -- the statement list is partitioned at branch /
   loop / exception boundaries (classified by the existing ``branch_kinds`` /
   ``loop_kinds`` / ``try_kinds`` / ``catch_kinds`` on ``LanguageNodeMap``).
3. **CFG builder** -- straight-line ``BasicBlock`` objects joined by
   successor / predecessor edges, with loop back-edges, branch join points,
   and synthetic entry / exit blocks.

The construction is *structured*: Python control flow nests cleanly (no
``goto``), so the graph is built recursively from the AST rather than from a
flat instruction stream with a leader scan. Each compound statement closes the
current straight-line block, builds its sub-structure, and the builder threads
a single fall-through block through to whatever follows.

**Determinism.** Blocks are emitted in a fixed pre-order (test block, then
then-branch, then alternatives, then the join; loop header, then body, then the
exit). Edge lists are appended in that order and de-duplicated. The same source
therefore yields byte-identical block ids and edges across runs -- a
prerequisite for stable trends and cache hits downstream.

**Language scope.** The block-structure logic is language-agnostic over
``LanguageNodeMap``: the conditional node kind (``if_kinds``), the statement-
container kinds (``block_kinds``), and the jump kinds (``return`` / ``raise`` /
``break`` / ``continue``) all come from the node-map, so the same builder serves
every full-tier language whose map populates them. Two grammar families are
handled transparently: Python encodes ``elif`` / ``else`` as sibling *clause*
children, while the C-family (Go / TS / Java / Rust) hangs the else arm off an
``alternative`` field -- the builder recognises both. A language that maps no
``if_kinds`` / ``block_kinds`` simply yields a straight-line CFG (degrade to
silence), the same precision-first default as the def/use dialect.

**Exceptions.** A ``try`` body, ``else`` clause or handler is joined to the
handlers / ``finally`` it can raise into from the state before it and from each
of its blocks. Those block edges are ``raise_edges``, since an exception can
leave a block mid-way: in ``x = 1; y = g(); x = 2`` the handler sees ``x = 1``.
The same walk records each ``try``'s clauses and each error exit with the
handler that catches it (:mod:`raises`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from ..complexity.languages import LanguageNodeMap
from .raises import (
    Handler,
    RaiseSite,
    TryRegion,
    catches,
    error_exit,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from .dialects.base import DefUseDialect

# Upper bound on basic blocks per function. A structured CFG has roughly one
# block per branch/loop arm, so a few hundred is already an extreme function;
# the cap is a backstop against pathological generated code, not a real limit.
# Tripping it returns no CFG for that function (degrade-to-silence).
_DEFAULT_MAX_BLOCKS = 5000


class CFGGuardTrippedError(Exception):
    """Raised internally when a function exceeds the basic-block cap.

    Caught at the harness boundary (:mod:`gating`) so the function is skipped
    with no CFG and no finding, never propagated to the caller.
    """


@dataclass(frozen=True)
class Statement:
    """One statement (or a compound statement's head) within a basic block.

    ``kind`` is the tree-sitter node type. For a compound construct that opens
    a block (``if`` / ``while`` / ``for``), only its *head* -- the condition or
    loop clause -- is recorded here; the body lives in successor blocks.
    """

    kind: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    # True when this records a compound construct's *head* (an ``if`` / ``while``
    # / ``for`` test) rather than a whole simple statement. Downstream def/use
    # extraction reads only the condition / loop clause for a head, since the
    # construct's body lives in successor blocks.
    is_head: bool = False
    # The originating tree-sitter node, kept for in-memory passes that need to
    # re-inspect the statement (def/use classification). Excluded from equality
    # and repr so block serialization and the determinism tests stay stable, and
    # because nodes are only valid while their parse tree is alive.
    node: Node | None = field(default=None, compare=False, repr=False)


@dataclass
class BasicBlock:
    """A maximal straight-line run of statements with a single entry/exit.

    ``kind`` labels the block's role so consumers (and tests) can reason about
    shape without re-deriving it:

    - ``entry`` / ``exit`` -- the synthetic function boundaries (no statements).
    - ``normal`` -- a straight-line run.
    - ``branch`` -- ends in a condition test with two successors.
    - ``loop_header`` -- a loop's test; one edge into the body, one past it,
      and a back-edge predecessor from the body tail.
    - ``loop_exit`` -- the join immediately after a loop.
    - ``join`` -- a merge point after an ``if`` / ``try``.
    - ``handler`` -- an ``except`` clause entry.
    - ``unreachable`` -- a block with no predecessor, created for statements
      that follow a terminator within the same sequence (dead code).
    """

    id: int
    kind: str = "normal"
    statements: list[Statement] = field(default_factory=list)
    successors: list[int] = field(default_factory=list)
    predecessors: list[int] = field(default_factory=list)

    @property
    def start_line(self) -> int:
        return min((s.start_line for s in self.statements), default=0)

    @property
    def end_line(self) -> int:
        return max((s.end_line for s in self.statements), default=0)

    @property
    def is_empty(self) -> bool:
        return not self.statements


@dataclass
class CFG:
    """A single function's control-flow graph.

    ``blocks`` is ordered by id (emission order). ``entry_id`` / ``exit_id``
    are the synthetic boundary blocks; every path from entry that does not
    diverge into an infinite loop reaches exit.
    """

    blocks: list[BasicBlock]
    entry_id: int
    exit_id: int
    function_name: str = ""
    function_start_line: int = 0
    # ``(src, dst)`` edges an exception can take from inside *src*, mid-block.
    raise_edges: frozenset[tuple[int, int]] = frozenset()
    # Error exits, ``try`` statements and Go ``defer`` lines, in source order.
    raises: tuple[RaiseSite, ...] = ()
    try_regions: tuple[TryRegion, ...] = ()
    defers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        self._by_id: dict[int, BasicBlock] = {b.id: b for b in self.blocks}
        self._at: dict[int, tuple[int, int]] | None = None

    @property
    def entry(self) -> BasicBlock:
        return self._by_id[self.entry_id]

    @property
    def exit(self) -> BasicBlock:
        return self._by_id[self.exit_id]

    def block(self, block_id: int) -> BasicBlock:
        return self._by_id[block_id]

    def statement_at(self, line: int) -> tuple[int, int] | None:
        """``(block id, index)`` of the narrowest statement holding *line*;
        None when no statement does. Built on first use."""
        if self._at is None:
            best: dict[int, tuple[int, int, int]] = {}
            for b in self.blocks:
                for k, st in enumerate(b.statements):
                    _narrowest(best, st, b.id, k)
            self._at = {ln: (bid, k) for ln, (_w, bid, k) in best.items()}
        return self._at.get(line)

    def successors(self, block: BasicBlock) -> list[BasicBlock]:
        return [self._by_id[s] for s in block.successors]

    def predecessors(self, block: BasicBlock) -> list[BasicBlock]:
        return [self._by_id[p] for p in block.predecessors]

    def reachable_ids(self) -> set[int]:
        """Block ids reachable from entry (forward BFS)."""
        seen: set[int] = set()
        stack = [self.entry_id]
        while stack:
            bid = stack.pop()
            if bid in seen:
                continue
            seen.add(bid)
            stack.extend(self._by_id[bid].successors)
        return seen

    def back_edges(self) -> list[tuple[int, int]]:
        """Edges ``(src, dst)`` where ``dst`` is a loop header re-entered from
        within its own body -- the loop back-edges, in id order."""
        out: list[tuple[int, int]] = []
        for b in self.blocks:
            if b.kind != "loop_header":
                continue
            out.extend((p, b.id) for p in sorted(b.predecessors) if p > b.id)
        return out


def _narrowest(best: dict[int, tuple[int, int, int]], st: Statement, bid: int, k: int) -> None:
    """Keep *st* (statement *k* of block *bid*) on each of its lines where it
    is narrower than what *best* holds."""
    width = st.end_line - st.start_line
    for ln in range(st.start_line, st.end_line + 1):
        if ln not in best or width < best[ln][0]:
            best[ln] = (width, bid, k)


@dataclass
class _LoopCtx:
    """Jump targets for ``break`` / ``continue`` within the enclosing loop."""

    continue_target: BasicBlock  # loop header (the back-edge destination)
    break_target: BasicBlock  # the block past the loop


class _CFGBuilder:
    """Recursive structured-CFG builder over one function's AST.

    The public entry is :func:`build_cfg`; this class holds the per-build
    mutable state (the block list, the id counter, and the loop-target stack).
    """

    def __init__(
        self, lmap: LanguageNodeMap, *, max_blocks: int, dialect: DefUseDialect | None = None
    ) -> None:
        self.lmap = lmap
        self.dialect = dialect
        self.max_blocks = max_blocks
        self.blocks: list[BasicBlock] = []
        self._next_id = 0
        self._loops: list[_LoopCtx] = []
        self.exit_block: BasicBlock | None = None
        self._raise_edges: set[tuple[int, int]] = set()
        # Handlers of each ``try`` whose body is being built, innermost last,
        # and the handler clauses being built: what a raise there meets.
        self._guards: list[tuple[Handler, ...]] = []
        self._handling: list[Handler] = []
        self._raises: list[RaiseSite] = []
        self._regions: list[TryRegion] = []
        self._defers: list[int] = []
        self._jump_kinds = (
            lmap.return_kinds | lmap.raise_kinds | lmap.break_kinds | lmap.continue_kinds
        )

    # -- low-level graph ops --------------------------------------------------

    def _new(self, kind: str = "normal") -> BasicBlock:
        if self._next_id >= self.max_blocks:
            raise CFGGuardTrippedError(self._next_id)
        block = BasicBlock(id=self._next_id, kind=kind)
        self._next_id += 1
        self.blocks.append(block)
        return block

    @staticmethod
    def _edge(src: BasicBlock | None, dst: BasicBlock | None) -> None:
        if src is None or dst is None:
            return
        if dst.id not in src.successors:
            src.successors.append(dst.id)
        if src.id not in dst.predecessors:
            dst.predecessors.append(src.id)

    def _edges_to(self, sources: list[BasicBlock], dst: BasicBlock) -> None:
        for src in sources:
            self._edge(src, dst)

    def _raises_to(self, sources: list[BasicBlock], dst: BasicBlock) -> None:
        self._edges_to(sources, dst)
        self._raise_edges.update((src.id, dst.id) for src in sources)

    def _protect(self, cur: BasicBlock, region: list[BasicBlock], dst: BasicBlock) -> None:
        """*cur* reaches *dst* by its end state, *region*'s blocks from mid-block."""
        self._edge(cur, dst)
        self._raises_to(region, dst)

    def _record_stmt(self, block: BasicBlock, node: Node) -> None:
        self._record_exit(node)
        block.statements.append(
            Statement(node.type, node.start_point[0] + 1, node.end_point[0] + 1, node=node)
        )

    def _record_head(self, block: BasicBlock, node: Node) -> None:
        """Record a compound statement's head (its condition / loop clause)."""
        cond = node.child_by_field_name("condition")
        line = node.start_point[0] + 1
        end = (cond.end_point[0] + 1) if cond is not None else line
        block.statements.append(Statement(node.type, line, end, is_head=True, node=node))

    # -- block / clause access ------------------------------------------------

    def _block_of(self, node: Node | None) -> Node | None:
        """The statement-container body of a clause (``consequence`` / ``body``
        field, or the first child whose kind is a ``block_kind`` for clauses with
        no body field, e.g. a Python ``except_clause``)."""
        if node is None:
            return None
        body = node.child_by_field_name("consequence") or node.child_by_field_name("body")
        if body is not None:
            return body
        for child in node.children:
            if child.type in self.lmap.block_kinds:
                return child
        return None

    def _body_stmts(self, block: Node | None) -> list[Node]:
        """The statement sequence inside a container, unwrapping a single nested
        statement-container (Go wraps a ``block``'s statements in a
        ``statement_list``; Python/TS expose them directly).

        Comment nodes are dropped: tree-sitter emits them as named siblings in
        the statement sequence (a trailing ``return x  # note`` puts the comment
        *after* the return), and a comment threaded through ``_process_seq``
        after a terminator would spawn a bogus ``unreachable`` block at the
        terminator's own line. The substring match covers every grammar's
        comment kinds (``comment`` / ``line_comment`` / ``block_comment`` /
        ``doc_comment``), the same idiom nloc counting uses."""
        if block is None:
            return []
        # A C-family arm without braces is the statement itself
        # (``if (x) throw e;``): its children are not statements.
        if block.type in self._jump_kinds:
            return [block]
        kids = [c for c in block.named_children if "comment" not in c.type]
        while len(kids) == 1 and kids[0].type in self.lmap.block_kinds:
            kids = [c for c in kids[0].named_children if "comment" not in c.type]
        return kids

    # -- the recursive walk ---------------------------------------------------

    def build(self, fn_node: Node) -> CFG:
        entry = self._new("entry")
        self.exit_block = self._new("exit")
        body = fn_node.child_by_field_name("body") or fn_node
        first = self._new()
        self._edge(entry, first)
        out = self._process_seq(self._body_stmts(body), first)
        if out is not None:
            self._edge(out, self.exit_block)
        return CFG(
            blocks=self.blocks,
            entry_id=entry.id,
            exit_id=self.exit_block.id,
            raise_edges=frozenset(self._raise_edges),
            raises=tuple(sorted(self._raises, key=lambda r: r.line)),
            try_regions=tuple(sorted(self._regions, key=lambda r: r.start_line)),
            defers=tuple(self._defers),
        )

    def _process_seq(self, stmts: list[Node], cur: BasicBlock | None) -> BasicBlock | None:
        """Thread *stmts* onto *cur*, returning the fall-through block.

        Returns ``None`` when control cannot fall through (the sequence ended
        in a terminator on every path). Compound statements close *cur* and the
        builder continues from the construct's join block.
        """
        assert self.exit_block is not None
        for st in stmts:
            # Expression-oriented grammars (Rust) wrap statement-position
            # control flow in a carrier node; classify the real statement.
            if st.type in self.lmap.statement_wrapper_kinds and st.named_child_count:
                st = st.named_children[-1]
            if cur is None:
                # Statements after a terminator: dead code with no predecessor.
                # Captured in an ``unreachable`` block so the statement is not
                # silently dropped, but it is reachable from nothing.
                cur = self._new("unreachable")
            t = st.type
            if t in self.lmap.if_kinds:
                cur = self._build_if(st, cur)
            elif t in self.lmap.loop_kinds:
                cur = self._build_loop(st, cur)
            elif t in self.lmap.try_kinds:
                cur = self._build_try(st, cur)
            elif t in self.lmap.with_kinds:
                cur = self._build_with(st, cur)
            elif t in self.lmap.return_kinds or t in self.lmap.raise_kinds:
                self._record_stmt(cur, st)
                self._edge(cur, self.exit_block)
                cur = None
            elif t in self.lmap.break_kinds:
                self._record_stmt(cur, st)
                if self._loops:
                    self._edge(cur, self._loops[-1].break_target)
                cur = None
            elif t in self.lmap.continue_kinds:
                self._record_stmt(cur, st)
                if self._loops:
                    self._edge(cur, self._loops[-1].continue_target)
                cur = None
            else:
                self._record_stmt(cur, st)
        return cur

    # -- error exits ----------------------------------------------------------

    def _record_exit(self, st: Node) -> None:
        """Record *st* as an error exit (or a ``defer``) when it is one."""
        if st.type == "defer_statement":
            self._defers.append(st.start_point[0] + 1)
            return
        if st.type in self.lmap.switch_kinds:
            self._record_arm_raises(st)
            return
        found = error_exit(st, self.lmap, self._handling[-1] if self._handling else None)
        if found is None:
            return
        kind, exc, raised = found
        caught = None
        if kind in ("raise", "rethrow"):
            guards = (h for hs in reversed(self._guards) for h in hs)
            caught = next((h.start_line for h in guards if catches(h.types, raised)), None)
        self._raises.append(RaiseSite(st.start_point[0] + 1, kind, exc, caught))

    def _record_arm_raises(self, switch: Node) -> None:
        """The raises and nested try regions in a ``switch`` / ``match`` the
        graph keeps whole. The statement is one graph node, so its arms are
        walked here, once, and only within it: nested functions and lambdas are
        not entered."""
        for child in switch.named_children:
            self._walk_arm_node(child)

    def _walk_arm_node(self, node: Node) -> None:
        scopes = self.lmap.function_kinds | self.lmap.lambda_kinds
        if node.type in self.lmap.raise_kinds:
            self._record_exit(node)
        elif node.type in self.lmap.try_kinds:
            clauses = [c for c in node.children if c.type in self.lmap.catch_kinds]
            heads = [self._handler(c) for c in clauses]
            body = node.child_by_field_name("body")
            else_clause = next((c for c in node.children if c.type == "else_clause"), None)
            finally_clause = next((c for c in node.children if c.type == "finally_clause"), None)

            self._guards.append(tuple(heads))
            if body is not None:
                for child in body.children:
                    self._walk_arm_node(child)
            self._guards.pop()

            if else_clause is not None:
                for child in else_clause.children:
                    self._walk_arm_node(child)

            handlers = []
            for clause, head in zip(clauses, heads, strict=True):
                self._handling.append(head)
                handler_entry = self._new("handler")
                handler_out = self._process_seq(
                    self._body_stmts(self._block_of(clause)), handler_entry
                )
                self._handling.pop()
                handlers.append(replace(head, completes=handler_out is not None))

            if finally_clause is not None:
                for child in finally_clause.children:
                    self._walk_arm_node(child)

            parts = (body, else_clause, clauses, finally_clause)
            self._regions.append(_try_region(node, parts, tuple(handlers)))
        elif node.type not in scopes:
            for child in node.named_children:
                self._walk_arm_node(child)

    def _build_if(self, if_node: Node, cur: BasicBlock) -> BasicBlock | None:
        cur.kind = "branch"
        self._record_head(cur, if_node)
        join = self._new("join")

        # then-branch
        then_entry = self._new()
        self._edge(cur, then_entry)
        then_block = if_node.child_by_field_name("consequence") or if_node.child_by_field_name("body")
        then_out = self._process_seq(self._body_stmts(then_block), then_entry)
        if then_out is not None:
            self._edge(then_out, join)

        # Two grammar families for the false arm:
        #  - Python / PHP flat chain: sibling clauses (``elif_clause`` /
        #    ``else_if_clause`` and a final ``else_clause``) children, all
        #    converging on one join.
        #  - everything else (a plain ``else``, or the C-family's nested
        #    ``else if``): a single else arm reached via the ``alternative`` field
        #    or an ``else_clause`` child.
        if any(c.type in ("elif_clause", "else_if_clause") for c in if_node.children):
            alts = [
                c
                for c in if_node.children
                if c.type in ("elif_clause", "else_if_clause", "else_clause")
            ]
            self._build_flat_else_chain(alts, cur, join)
        else:
            self._build_c_alternative(if_node, cur, join)
        return join if join.predecessors else None

    def _build_flat_else_chain(self, alts: list[Node], cur: BasicBlock, join: BasicBlock) -> None:
        """Flat ``elif`` / ``else_if`` / ``else`` clause chain off *cur*'s false edge."""
        false_src: BasicBlock | None = cur
        for alt in alts:
            if alt.type in ("elif_clause", "else_if_clause"):
                elif_test = self._new("branch")
                self._record_head(elif_test, alt)
                self._edge(false_src, elif_test)
                elif_entry = self._new()
                self._edge(elif_test, elif_entry)
                body = (
                    alt.child_by_field_name("consequence")
                    or alt.child_by_field_name("body")
                    or self._block_of(alt)
                )
                elif_out = self._process_seq(self._body_stmts(body), elif_entry)
                if elif_out is not None:
                    self._edge(elif_out, join)
                false_src = elif_test
            else:  # else_clause
                else_entry = self._new()
                self._edge(false_src, else_entry)
                body = (
                    alt.child_by_field_name("body")
                    or alt.child_by_field_name("consequence")
                    or self._block_of(alt)
                )
                else_out = self._process_seq(self._body_stmts(body), else_entry)
                if else_out is not None:
                    self._edge(else_out, join)
                false_src = None
        # No ``else``: the last test's false edge falls straight through.
        if false_src is not None:
            self._edge(false_src, join)

    def _build_c_alternative(self, if_node: Node, cur: BasicBlock, join: BasicBlock) -> None:
        """The single else arm off *cur*'s false edge (non-flat-``elif`` case).

        Covers a plain ``else`` (Python / Go / TS), a TS / Python ``else_clause``
        wrapping that block or a nested ``if``, and the C-family ``else if``
        chain. A nested ``if`` is threaded through :meth:`_process_seq` so it
        recurses into :meth:`_build_if` and converges on the same *join*; a plain
        block is unwrapped to its statements. No alternative => the false edge
        falls straight through to *join*."""
        alt = if_node.child_by_field_name("alternative")
        if alt is None:  # Python's else arm is an ``else_clause`` child, no field
            alt = next((c for c in if_node.children if c.type == "else_clause"), None)
        if alt is None:
            self._edge(cur, join)
            return
        if alt.type == "else_clause":  # Python / TS wrap the else arm in a clause
            # Comment nodes are named siblings here too, and an ``else:`` whose only
            # child is a comment has no statements at all. Picking a comment would
            # make it ``alt``, so the kind is never an ``if_kinds`` one and it reaches
            # ``_process_seq`` as a statement, giving the block the comment's own start
            # line. Skip them the same way ``_body_stmts`` does.
            inner = next(
                (c for c in alt.named_children if "comment" not in c.type), None
            )
            if inner is None:
                self._edge(cur, join)
                return
            alt = inner
        else_entry = self._new()
        self._edge(cur, else_entry)
        # ``else if`` -> a nested branch; a plain else block -> its statements.
        stmts = [alt] if alt.type in self.lmap.if_kinds else self._body_stmts(alt)
        else_out = self._process_seq(stmts, else_entry)
        if else_out is not None:
            self._edge(else_out, join)

    def _build_loop(self, loop_node: Node, cur: BasicBlock) -> BasicBlock:
        header = self._new("loop_header")
        self._record_head(header, loop_node)
        self._edge(cur, header)
        after = self._new("loop_exit")

        body_entry = self._new()
        self._edge(header, body_entry)  # enter the loop body

        self._loops.append(_LoopCtx(continue_target=header, break_target=after))
        body_out = self._process_seq(
            self._body_stmts(loop_node.child_by_field_name("body")), body_entry
        )
        self._loops.pop()

        if body_out is not None:
            self._edge(body_out, header)  # back-edge

        # Python's for/while else runs on exhaustion, but not on break. Keep
        # break targeting ``after`` while routing the header's exhaustion edge
        # through the else body. Loops without an alternative keep their direct edge.
        alternative = loop_node.child_by_field_name("alternative")
        if alternative is None:
            self._edge(header, after)
        else:
            else_entry = self._new()
            self._edge(header, else_entry)
            else_out = self._process_seq(self._body_stmts(self._block_of(alternative)), else_entry)
            if else_out is not None:
                self._edge(else_out, after)
        return after

    def _build_with(self, with_node: Node, cur: BasicBlock) -> BasicBlock | None:
        """Process a context-manager head and recurse into its body.

        A with-body always executes. If every path in the body terminates,
        statements following the with-statement cannot fall through.
        """
        self._record_head(cur, with_node)

        body_entry = self._new()
        self._edge(cur, body_entry)

        body_out = self._process_seq(
            self._body_stmts(with_node.child_by_field_name("body")),
            body_entry,
        )
        if body_out is None:
            return None

        join = self._new("join")
        self._edge(body_out, join)
        return join

    def _build_try(self, try_node: Node, cur: BasicBlock) -> BasicBlock:
        # try-with-resources (Java) binds locals in its resource clause; record
        # a head so the def/use dialect sees those bindings. A plain ``try``
        # has no ``resources`` field and records nothing (unchanged).
        if try_node.child_by_field_name("resources") is not None:
            self._record_head(cur, try_node)
        join = self._new("join")

        # ``finally`` is the convergence point everything funnels through; with
        # no ``finally`` the join itself is that point.
        finally_clause = next((c for c in try_node.children if c.type == "finally_clause"), None)
        normal_join: BasicBlock = join
        if finally_clause is not None:
            fin_entry = self._new()
            fin_out = self._process_seq(self._body_stmts(self._block_of(finally_clause)), fin_entry)
            if fin_out is not None:
                self._edge(fin_out, join)
            normal_join = fin_entry

        # try body (the protected region). An exception may leave it before
        # its first statement completes or from any block inside it, so the
        # state before the body and at the end of every body block reaches the
        # handlers and the ``finally`` (which also runs on a body ``return``).
        # Blocks are numbered in creation order: a region's blocks are the
        # ones created from its first id on.
        clauses = [c for c in try_node.children if c.type in self.lmap.catch_kinds]
        heads = [self._handler(c) for c in clauses]
        first = len(self.blocks)
        body_entry = self._new()
        self._edge(cur, body_entry)
        body = try_node.child_by_field_name("body")
        self._guards.append(tuple(heads))
        body_out = self._process_seq(self._body_stmts(body), body_entry)
        self._guards.pop()
        protected = self.blocks[first:]
        if finally_clause is not None:
            self._protect(cur, protected, normal_join)

        # ``else`` runs only when the body completed without an exception; one
        # raised inside it skips the handlers but still runs the ``finally``.
        else_clause = next((c for c in try_node.children if c.type == "else_clause"), None)
        if else_clause is not None and body_out is not None:
            first = len(self.blocks)
            else_entry = self._new()
            self._edge(body_out, else_entry)
            else_out = self._process_seq(self._body_stmts(self._block_of(else_clause)), else_entry)
            if else_out is not None:
                self._edge(else_out, normal_join)
            if finally_clause is not None:
                self._raises_to(self.blocks[first:], normal_join)
        elif body_out is not None:
            self._edge(body_out, normal_join)

        handlers = self._build_handlers(clauses, heads, cur, protected, normal_join, finally_clause)
        parts = (body, else_clause, clauses, finally_clause)
        self._regions.append(_try_region(try_node, parts, handlers))
        return join

    def _build_handlers(
        self,
        clauses: list[Node],
        heads: list[Handler],
        cur: BasicBlock,
        protected: list[BasicBlock],
        normal_join: BasicBlock,
        finally_clause: Node | None,
    ) -> tuple[Handler, ...]:
        """Each handler clause of a ``try``, reached from its *protected*
        body; returns them with whether each can complete normally."""
        handlers = []
        for handler, head in zip(clauses, heads, strict=True):
            first = len(self.blocks)
            handler_entry = self._new("handler")
            # The clause head (``except E as e:``) reads the exception type and
            # binds its name; the dialect says which (``handler_def_use``).
            self._record_head(handler_entry, handler)
            self._protect(cur, protected, handler_entry)
            self._handling.append(head)
            handler_out = self._process_seq(
                self._body_stmts(self._block_of(handler)), handler_entry
            )
            self._handling.pop()
            handlers.append(replace(head, completes=handler_out is not None))
            if handler_out is not None:
                self._edge(handler_out, normal_join)
            # A handler that returns or raises part-way still runs ``finally``.
            if finally_clause is not None:
                self._raises_to(self.blocks[first:], normal_join)
        return tuple(handlers)

    def _handler(self, clause: Node) -> Handler:
        """What *clause* names and binds, from the language's dialect; with
        none, a handler whose types match no raise."""
        types, name = self.dialect.handler_head(clause) if self.dialect else ((), None)
        return Handler(clause.start_point[0] + 1, clause.end_point[0] + 1, types, name)


def _try_region(
    try_node: Node,
    parts: tuple[Node | None, Node | None, list[Node], Node | None],
    handlers: tuple[Handler, ...],
) -> TryRegion:
    """*try_node*'s lines and its clauses': *parts* is its body, ``else``,
    handler clauses and ``finally``."""
    body, else_clause, clauses, finally_clause = parts
    nodes = [body, else_clause, *clauses, finally_clause]
    return TryRegion(
        try_node.start_point[0] + 1,
        try_node.end_point[0] + 1,
        _lines(body) or (0, 0),
        tuple(x for x in map(_lines, nodes) if x is not None),
        handlers,
        _lines(finally_clause),
    )


def _lines(node: Node | None) -> tuple[int, int] | None:
    return (node.start_point[0] + 1, node.end_point[0] + 1) if node is not None else None


def build_cfg(
    fn_node: Node,
    lmap: LanguageNodeMap,
    *,
    max_blocks: int = _DEFAULT_MAX_BLOCKS,
    dialect: DefUseDialect | None = None,
) -> CFG:
    """Build the control-flow graph for one function AST node.

    *fn_node* is a function/method definition node (the kind
    ``_collect_function_nodes`` yields); *lmap* is its language's
    ``LanguageNodeMap``. Raises :class:`CFGGuardTrippedError` if the function
    exceeds *max_blocks* -- callers building over a corpus should catch it and
    skip the function (the :mod:`gating` harness does). *dialect* says what
    each exception handler names (``handler_head``); without it no raise is
    counted as caught.
    """
    return _CFGBuilder(lmap, max_blocks=max_blocks, dialect=dialect).build(fn_node)

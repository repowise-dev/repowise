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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..complexity.languages import LanguageNodeMap

if TYPE_CHECKING:
    from tree_sitter import Node

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

    def __post_init__(self) -> None:
        self._by_id: dict[int, BasicBlock] = {b.id: b for b in self.blocks}

    @property
    def entry(self) -> BasicBlock:
        return self._by_id[self.entry_id]

    @property
    def exit(self) -> BasicBlock:
        return self._by_id[self.exit_id]

    def block(self, block_id: int) -> BasicBlock:
        return self._by_id[block_id]

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

    def __init__(self, lmap: LanguageNodeMap, *, max_blocks: int) -> None:
        self.lmap = lmap
        self.max_blocks = max_blocks
        self.blocks: list[BasicBlock] = []
        self._next_id = 0
        self._loops: list[_LoopCtx] = []
        self.exit_block: BasicBlock | None = None

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

    def _record_stmt(self, block: BasicBlock, node: Node) -> None:
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
        return CFG(blocks=self.blocks, entry_id=entry.id, exit_id=self.exit_block.id)

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

    def _build_if(self, if_node: Node, cur: BasicBlock) -> BasicBlock:
        cur.kind = "branch"
        self._record_head(cur, if_node)
        join = self._new("join")

        # then-branch
        then_entry = self._new()
        self._edge(cur, then_entry)
        then_out = self._process_seq(
            self._body_stmts(if_node.child_by_field_name("consequence")), then_entry
        )
        if then_out is not None:
            self._edge(then_out, join)

        # Two grammar families for the false arm:
        #  - Python's *flat* ``elif`` chain: sibling ``elif_clause`` (and a final
        #    ``else_clause``) children, all converging on one join.
        #  - everything else (a plain ``else``, or the C-family's nested
        #    ``else if``): a single else arm reached via the ``alternative`` field
        #    or an ``else_clause`` child. ``else_clause`` is shared between Python
        #    and TS, so the discriminator is specifically an ``elif_clause``.
        if any(c.type == "elif_clause" for c in if_node.children):
            alts = [c for c in if_node.children if c.type in ("elif_clause", "else_clause")]
            self._build_py_else_chain(alts, cur, join)
        else:
            self._build_c_alternative(if_node, cur, join)
        return join

    def _build_py_else_chain(self, alts: list[Node], cur: BasicBlock, join: BasicBlock) -> None:
        """Python ``elif`` / ``else`` clause chain off *cur*'s false edge."""
        false_src: BasicBlock | None = cur
        for alt in alts:
            if alt.type == "elif_clause":
                elif_test = self._new("branch")
                self._record_head(elif_test, alt)
                self._edge(false_src, elif_test)
                elif_entry = self._new()
                self._edge(elif_test, elif_entry)
                elif_out = self._process_seq(
                    self._body_stmts(alt.child_by_field_name("consequence")), elif_entry
                )
                if elif_out is not None:
                    self._edge(elif_out, join)
                false_src = elif_test
            else:  # else_clause
                else_entry = self._new()
                self._edge(false_src, else_entry)
                else_out = self._process_seq(self._body_stmts(self._block_of(alt)), else_entry)
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
            inner = next((c for c in alt.named_children), None)
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
        self._edge(header, after)  # skip / exhaust the loop

        self._loops.append(_LoopCtx(continue_target=header, break_target=after))
        body_out = self._process_seq(
            self._body_stmts(loop_node.child_by_field_name("body")), body_entry
        )
        self._loops.pop()

        if body_out is not None:
            self._edge(body_out, header)  # back-edge
        return after

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

        # try body (the protected region)
        body_entry = self._new()
        self._edge(cur, body_entry)
        # The finally body runs on every path out of the protected region,
        # including abrupt exits (a body / handler ``return`` or ``raise``)
        # that never reach the normal join. Approximate with an edge from the
        # region entry, mirroring the handler edges below; without it a
        # ``finally`` after an always-returning body is flagged unreachable.
        if finally_clause is not None:
            self._edge(body_entry, normal_join)
        body_out = self._process_seq(
            self._body_stmts(try_node.child_by_field_name("body")), body_entry
        )

        # ``else`` runs only when the body completed without an exception.
        else_clause = next((c for c in try_node.children if c.type == "else_clause"), None)
        if else_clause is not None and body_out is not None:
            else_entry = self._new()
            self._edge(body_out, else_entry)
            else_out = self._process_seq(self._body_stmts(self._block_of(else_clause)), else_entry)
            if else_out is not None:
                self._edge(else_out, normal_join)
        elif body_out is not None:
            self._edge(body_out, normal_join)

        # except handlers are reachable from the protected region (an exception
        # may escape any statement in the body -> approximate with an edge from
        # the body entry to each handler).
        for handler in (c for c in try_node.children if c.type in self.lmap.catch_kinds):
            handler_entry = self._new("handler")
            self._edge(body_entry, handler_entry)
            handler_out = self._process_seq(
                self._body_stmts(self._block_of(handler)), handler_entry
            )
            if handler_out is not None:
                self._edge(handler_out, normal_join)

        return join


def build_cfg(
    fn_node: Node,
    lmap: LanguageNodeMap,
    *,
    max_blocks: int = _DEFAULT_MAX_BLOCKS,
) -> CFG:
    """Build the control-flow graph for one function AST node.

    *fn_node* is a function/method definition node (the kind
    ``_collect_function_nodes`` yields); *lmap* is its language's
    ``LanguageNodeMap``. Raises :class:`CFGGuardTrippedError` if the function
    exceeds *max_blocks* -- callers building over a corpus should catch it and
    skip the function (the :mod:`gating` harness does).
    """
    return _CFGBuilder(lmap, max_blocks=max_blocks).build(fn_node)

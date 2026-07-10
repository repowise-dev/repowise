"""The ``PerfDialect`` plugin contract + the ``PERF_DIALECTS`` registry.

The performance pass (``complexity/perf_walk.py::_collect_perf_hits``) is
language-agnostic: every language difference lives in a ``PerfDialect``. This
mirrors the per-language plugin idiom the rest of the pipeline already uses
(``resolvers/``, ``extractors/bindings/``, ``heritage/``, ``framework_edges/``,
``workspace/extractors/http/``) — one module per language, registered in a dict,
zero edits to the orchestrator to add one.

A dialect owns the *semantic* layer the walker cannot generalise:

================================  ============================================
Member                            What it answers
================================  ============================================
``callee_root_name(node)``        ``a.b.c()`` -> ``"a"`` (the per-grammar seam)
``callee_method_name(node)``      ``x.execute()`` -> ``"execute"``
``callee_is_attribute(node)``     is the callee a member access vs a bare call?
``sink_kind(...)``                is this call an *execution sink* at an I/O
                                  boundary (db / network / fs / subprocess)?
``is_constant_loop(node)``        is this loop's bound a compile-time constant
                                  (so it is not data-dependent N+1)?
``is_string_concat(node)``        is this a ``+=`` string accumulation?
``is_async_fn(node)``             does this function carry an ``async`` modifier
                                  token (combined with ``lmap.async_function_kinds``)?
``blocking_sync_api(root, m)``    the offending name if ``root.m()`` is a known
                                  blocking sync call (sync-in-async).
``markers``                       the marker kinds this dialect can emit — lets
                                  Go add ``defer_in_loop`` and Java/Go add
                                  ``regex_compile_in_loop`` without touching the
                                  walker.
================================  ============================================

Four *optional* hooks let a language emit markers beyond the original three,
each defaulting to "no signal" so a language that does not set them is byte-for-
byte unchanged:

================================  ============================================
``loop_call_marker(root, m, n,    a loop-nested *call* that is a non-I/O marker
``list_names)``                   (``regexp.MustCompile`` / ``Pattern.compile`` ·
                                  ``sqlite3.connect`` resource construction ·
                                  ``lock.acquire`` contention ·
                                  ``big_list``-bound ``arr.includes`` membership).
``loop_stmt_marker(node,          a loop-nested *non-call statement* marker
``list_names)``                   (Go ``defer`` · C# ``lock(x){}`` · ``new
                                  HttpClient()`` resource construction · Python
                                  ``x in big_list`` membership test).
``async_blocking_member(node)``   a non-call member read that blocks in async
                                  (C# ``task.Result``).
``list_bound_names(root)``        names provably bound to a list literal /
                                  comprehension in this file — the gate for the
                                  ``membership_test_against_list_in_loop`` marker.
================================  ============================================

The ``list_names`` argument on the two loop-marker hooks is the frozenset
returned by :meth:`list_bound_names` (computed once per file by the walker only
when a dialect lists the membership marker); it lets a marker fire ``x in
big_list`` only when ``big_list`` is a known list, not a set/dict that membership
tests cheaply.

Every method has a safe default on :class:`BasePerfDialect`, so an unmapped
language (no entry in ``PERF_DIALECTS``) produces no perf signal at all, and a
mapped language that does not override a method gets "no signal" for that facet
rather than a wrong guess. This is the precision-first contract the whole perf
pillar depends on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

# Node types whose callee is a member access (``x.foo()``) rather than a bare
# identifier call. Covers Python ``attribute``, TS ``member_expression``, C++/
# Rust ``field_expression``, Go ``selector_expression``. Languages that need
# more (C# ``member_access_expression``) extend this; languages whose call node
# has no wrapping member-access node at all (Java ``method_invocation``)
# override :meth:`callee_is_attribute` outright.
_ATTRIBUTE_CALLEE_KINDS: frozenset[str] = frozenset(
    {"attribute", "member_expression", "field_expression", "selector_expression"}
)


class BasePerfDialect:
    """Default ``PerfDialect`` implementation — every method is "no signal".

    Language modules subclass this and override only what differs. The generic
    callee extraction here works for Python / TS / JS / Go (field-name based);
    Java and C# extend or replace it. All the *semantic* predicates default to
    "no signal" so a new language is opt-in facet by facet.
    """

    #: Language tag this dialect serves (informational; the registry is the
    #: source of truth for dispatch).
    language: str = ""

    #: Marker kinds this dialect can emit. The walker consults this before
    #: attempting each marker, so an unlisted marker's detection code never
    #: runs for this language.
    markers: frozenset[str] = frozenset()

    #: Callee node types treated as member access by :meth:`callee_is_attribute`.
    attribute_callee_kinds: frozenset[str] = _ATTRIBUTE_CALLEE_KINDS

    #: String-literal node kinds + compound-assignment node kinds for the
    #: generic :meth:`is_string_concat`. Empty -> the predicate is always False.
    string_literal_kinds: frozenset[str] = frozenset()
    aug_assign_kinds: frozenset[str] = frozenset()

    # -- callee extraction (the per-grammar seam) -----------------------------

    def callee_root_name(self, call_node: Node) -> str | None:
        """Root identifier of a call's callee: ``a.b.c()`` -> 'a', ``foo()`` -> 'foo'."""
        fn = call_node.child_by_field_name("function")
        if fn is None:
            named = [c for c in call_node.children if c.is_named]
            fn = named[0] if named else None
        if fn is None:
            return None
        node = fn
        for _ in range(8):
            if node.type in ("identifier", "property_identifier", "field_identifier"):
                break
            obj = node.child_by_field_name("object") or node.child_by_field_name("value")
            if obj is None:
                named = [c for c in node.children if c.is_named]
                if not named:
                    break
                node = named[0]
            else:
                node = obj
        txt = (node.text or b"").decode("utf-8", "replace")
        return txt.split(".")[0] if txt else None

    def callee_method_name(self, call_node: Node) -> str | None:
        """Rightmost member of the callee (``x.execute`` -> 'execute')."""
        fn = call_node.child_by_field_name("function")
        if fn is None:
            return None
        prop = (
            fn.child_by_field_name("property")
            or fn.child_by_field_name("field")
            or fn.child_by_field_name("attribute")
        )
        if prop is not None and prop.text:
            return prop.text.decode("utf-8", "replace")
        if fn.type == "identifier" and fn.text:
            return fn.text.decode("utf-8", "replace")
        ids = [c for c in fn.children if c.type == "identifier"]
        if ids and ids[-1].text:
            return ids[-1].text.decode("utf-8", "replace")
        return None

    def callee_is_attribute(self, call_node: Node) -> bool:
        """True if the callee is a member access (``x.foo()``), not a bare call."""
        fn = call_node.child_by_field_name("function")
        if fn is None:
            return False
        return fn.type in self.attribute_callee_kinds

    # -- sink classification (the lexicon) ------------------------------------

    def sink_kind(
        self,
        root: str,
        method: str,
        *,
        awaited: bool,
        is_attribute: bool,
        io_names: dict[str, str],
        has_db_import: bool,
    ) -> str | None:
        """Boundary kind (db / network / filesystem / subprocess) if this call
        is an *execution sink*, else ``None`` ("not an I/O round-trip")."""
        return None

    # -- loop / string / async predicates -------------------------------------

    def is_constant_loop(self, node: Node) -> bool:
        """True if this loop's bound is a compile-time constant (not N+1)."""
        return False

    def block_loop_body(self, node: Node) -> Node | None:
        """The per-iteration body when *node* is a call-with-block the language
        counts as a loop (Ruby ``items.each do |x| ... end``), else ``None``.

        The shared answer to combinator/block iteration: languages whose real
        loop idiom is a method call taking a block (Ruby ``.each``/``.map``,
        and later Scala ``.foreach``, Kotlin ``forEach``, Dart ``forEach``)
        override this to return the block node when the callee is a known
        full-iteration combinator AND a block argument is present. The walker
        then treats the call as a loop whose body is exactly the returned
        node — the receiver and arguments still run once, mirroring the
        loop-BODY scoping rule for native loops. Only *statically certain*
        iteration counts: a combinator without an inline block (``.map(&:f)``)
        returns ``None`` because there is no per-iteration body to scan.

        Default ``None`` — a dialect that does not override this is
        byte-for-byte unchanged (the walker skips the hook entirely unless it
        is overridden).
        """
        return None

    def is_iteration_loop(self, node: Node) -> bool:
        """True if this loop iterates a *collection* (a data multiplier), rather
        than spinning a cursor (``while (hasMore)`` / ``for (;;)``).

        The precision lever for the nested-loop markers: only when the OUTER
        loop multiplies over a collection is an inner I/O sink genuinely O(n*m).
        A pagination ``while`` cursor wrapping an inner ``for ... of chunk`` is
        ``io_in_loop``, not a nested round-trip explosion (Phase-7c TS corpus).
        Default ``True`` — a language that does not distinguish keeps today's
        behavior byte-for-byte; TS/JS overrides this to exclude ``while`` /
        C-style ``for`` cursors.
        """
        return True

    @staticmethod
    def _dotted_path(node: Node | None) -> str | None:
        """The text of *node* if it is a stable dotted path (``items`` /
        ``self.items`` / ``a.b.c``) with no call / subscript / index, else None.

        Lets the same-collection ``nested_loop_quadratic`` gate compare two
        loops iterating ``self.items`` — not only bare locals — while excluding
        ``get_items()`` / ``rows[i]`` which are not stable identities."""
        if node is None or node.text is None:
            return None
        txt = node.text.decode("utf-8", "replace")
        if not txt or not (txt[0].isalpha() or txt[0] == "_"):
            return None
        return txt if all(c.isalnum() or c in "_." for c in txt) else None

    def loop_iterable_name(self, node: Node) -> str | None:
        """Bare identifier this loop iterates over (``for x in items`` -> 'items'),
        or ``None`` when it is not a simple name.

        The gate for the same-collection ``nested_loop_quadratic`` shape: two
        nested loops over the SAME named collection are an all-pairs O(n^2) site
        (the high-precision shape that replaces raw nesting depth). Default
        ``None`` so a language that does not override it never fires the shaped
        marker. Precision-first by construction.
        """
        return None

    def is_string_concat(self, node: Node) -> bool:
        """True if *node* is a ``+=`` accumulation onto a string."""
        if not self.aug_assign_kinds or node.type not in self.aug_assign_kinds:
            return False
        if not any(c.type == "+=" for c in node.children):
            return False
        return self._rhs_is_stringish(node)

    def _rhs_is_stringish(self, node: Node) -> bool:
        """True if an augmented-assignment's RHS is provably string-typed.

        Precision-first: only a string/template literal directly on the RHS (or
        as an operand of a ``+`` on the RHS) counts. ``s += chunk`` where
        ``chunk`` is an opaque variable is NOT flagged.
        """
        right = node.child_by_field_name("right")
        if right is None:
            return False
        kinds = self.string_literal_kinds
        if right.type in kinds:
            return True
        if right.type in ("binary_operator", "binary_expression"):
            return any(c.is_named and c.type in kinds for c in right.children)
        return False

    def is_async_fn(self, node: Node) -> bool:
        """True if a function node carries an ``async`` modifier token.

        Combined by the walker with ``lmap.async_function_kinds`` (the
        dedicated async node types). The default sniffs for a child of *type*
        ``async`` — the shape Python (``async def`` is a ``function_definition``
        with an ``async`` child) and TS/JS (``async`` arrow/function) both use.
        Languages whose async marker is a modifier *token text* rather than a
        node type (C# ``async``) override this.
        """
        return any(c.type == "async" for c in node.children)

    def blocking_sync_api(self, root: str, method: str) -> str | None:
        """The offending API name if ``root.method`` is a known blocking sync
        call that stalls an event loop inside an async function, else ``None``."""
        return None

    # -- optional extra-marker hooks (default: no signal) ---------------------

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        """Marker kind for a loop-nested *call* that is not an I/O sink.

        Used for ``regex_compile_in_loop`` (``Pattern.compile`` /
        ``regexp.MustCompile`` recompiled every iteration), the per-iteration
        heavy-client construction ``resource_construction_in_loop``
        (``sqlite3.connect`` / ``boto3.client``), the lock-contention
        ``lock_in_loop`` (``lock.acquire`` / ``mu.Lock``), and the JS/TS
        ``arr.includes`` form of ``membership_test_against_list_in_loop`` (gated
        on ``root in list_names``). Default ``None``.
        """
        return None

    def bare_call_marker(self, root: str, method: str, node: Node) -> str | None:
        """Marker kind for a *call* that is its own iteration construct, so it is
        a perf smell at ANY loop depth (not only inside an outer loop).

        Used for ``array_spread_in_reduce`` (``arr.reduce((a,x)=>[...a,x], [])``
        rebuilds the accumulator every step -> O(n^2)): the ``.reduce`` IS the
        loop, so unlike :meth:`loop_call_marker` this fires without an enclosing
        loop. Default ``None`` so a language that does not override it is
        byte-for-byte unchanged.
        """
        return None

    def loop_iterable_call_marker(self, node: Node) -> str | None:
        """Marker kind for a loop whose *iterable expression is itself a call*.

        Used for ``pandas_iterrows_in_loop`` (``for _, row in df.iterrows()``):
        the offending call sits in the loop HEADER, so it runs once and the body
        :meth:`loop_call_marker` (loop_depth >= 1) never sees it. This hook is
        handed the loop node itself, so the dialect inspects the iterable and
        fires regardless of nesting depth (``iterrows`` is O(n)-boxing slow on
        its own, not only when nested). Default ``None`` so a language that does
        not override it is byte-for-byte unchanged.
        """
        return None

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        """Marker kind for a loop-nested *non-call statement* node.

        Used for Go ``defer_in_loop`` (a ``defer`` inside a loop leaks the
        deferred handle until the enclosing function returns), C# ``lock(x){}``
        / Java ``synchronized`` blocks (``lock_in_loop``), constructor nodes the
        language does not route through ``call_kinds`` (TS ``new PrismaClient``
        / C# ``new HttpClient`` -> ``resource_construction_in_loop``), and the
        Python ``x in big_list`` comparison form of
        ``membership_test_against_list_in_loop`` (gated on ``list_names``).
        Default ``None``.
        """
        return None

    def list_bound_names(self, root: Node) -> frozenset[str]:
        """Names provably bound to a *list* in this file (literal / comprehension
        / ``list(...)`` / ``sorted(...)``).

        The precision gate for ``membership_test_against_list_in_loop``: an
        ``x in name`` test is O(n) per probe only when ``name`` is a list — a set
        or dict membership test is O(1) and must not fire. Precision-first: only
        bindings whose RHS is *provably* a list count, so an opaque
        ``name = build()`` never enables the marker. Default: empty (the marker
        cannot fire for a language that does not override this).
        """
        return frozenset()

    def async_blocking_member(self, node: Node) -> str | None:
        """The offending name if *node* is a non-call member read that blocks
        the event loop inside an async function (C# ``task.Result``), else
        ``None``. The call forms (``.Wait()`` / ``.GetResult()``) go through
        :meth:`blocking_sync_api` instead. Default ``None``.
        """
        return None

    def is_lock_scope(self, node: Node) -> bool:
        """True if *node* opens a block-scoped held-lock region.

        The walker tracks a ``lock_depth`` analogous to ``loop_depth`` so the
        ``blocking_io_under_lock`` marker can fire on an I/O sink reached while a
        lock is held. Only unambiguous *block* constructs qualify — C#
        ``lock (x) { ... }`` and Java ``synchronized (x) { ... }`` — where the
        held region is exactly the node's body. Acquire/release *pairs*
        (``lock.acquire()`` … ``lock.release()``) are not block-scoped and are
        deliberately out of scope here (no held-region node to bound), so this
        defaults to ``False`` and a language that does not override it produces
        no lock-scope signal. Precision-first by construction.
        """
        return False


# The registry, populated by ``dialects/__init__.py`` from each language module.
# Keyed by ``LanguageTag``; a missing key ⇒ the perf pass is silent for that
# language (no dialect = no signal).
PERF_DIALECTS: dict[str, BasePerfDialect] = {}

"""The ``PerfDialect`` plugin contract + the ``PERF_DIALECTS`` registry.

The performance pass (``complexity/walker.py::_collect_perf_hits``) is
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

Three *optional* hooks let a language emit markers beyond the original three,
each defaulting to "no signal" so a language that does not set them is byte-for-
byte unchanged:

================================  ============================================
``loop_call_marker(root, m, n)``  a loop-nested *call* that is a non-I/O marker
                                  (``regexp.MustCompile`` / ``Pattern.compile``).
``loop_stmt_marker(node)``        a loop-nested *non-call statement* marker
                                  (Go ``defer`` in a loop -> handle leak).
``async_blocking_member(node)``   a non-call member read that blocks in async
                                  (C# ``task.Result``).
================================  ============================================

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

    def loop_call_marker(self, root: str, method: str, node: Node) -> str | None:
        """Marker kind for a loop-nested *call* that is not an I/O sink.

        Used for ``regex_compile_in_loop`` (``Pattern.compile`` /
        ``regexp.MustCompile`` recompiled every iteration). Default ``None``.
        """
        return None

    def loop_stmt_marker(self, node: Node) -> str | None:
        """Marker kind for a loop-nested *non-call statement* node.

        Used for Go ``defer_in_loop`` (a ``defer`` inside a loop leaks the
        deferred handle until the enclosing function returns). Default ``None``.
        """
        return None

    def async_blocking_member(self, node: Node) -> str | None:
        """The offending name if *node* is a non-call member read that blocks
        the event loop inside an async function (C# ``task.Result``), else
        ``None``. The call forms (``.Wait()`` / ``.GetResult()``) go through
        :meth:`blocking_sync_api` instead. Default ``None``.
        """
        return None


# The registry, populated by ``dialects/__init__.py`` from each language module.
# Keyed by ``LanguageTag``; a missing key ⇒ the perf pass is silent for that
# language (no dialect = no signal).
PERF_DIALECTS: dict[str, BasePerfDialect] = {}

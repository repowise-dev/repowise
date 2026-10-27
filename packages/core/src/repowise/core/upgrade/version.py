"""Authoritative version constants for the on-disk store format.

These two integers are the *single source of truth* for "what shape is the
``.repowise`` store, and does upgrading repowise need to touch it." They are
deliberately decoupled from the package ``__version__`` so that ordinary
releases (which ship code changes but no store-format change) never invalidate
a user's cached work.

When to bump
------------
``STORE_FORMAT_VERSION``
    Bump by one whenever the *meaning or layout* of the persisted store changes
    in a way the running code must react to (a new required column the additive
    reconcile cannot back-fill, a vector-store layout change, a re-derivation
    that older stores lack). Every bump MUST add a matching entry to
    :data:`repowise.core.upgrade.registry.MIGRATIONS` declaring its upgrade
    impact tier. The overwhelming default for a release is to leave this alone.

``PARSER_SCHEMA_VERSION``
    Bump by one only when parser / extractor logic changes such that a cached
    ``ParsedFile`` from an older build is no longer correct (tree-sitter query
    edits are already covered by hashing the ``.scm`` sources; bump this for
    Python-side extraction changes). Bumping it invalidates the parse cache and
    forces a cheap, automatic re-parse on the next ingest. Leaving package
    ``__version__`` out of the fingerprint is the whole point: unrelated
    releases keep the cache warm.
"""

from __future__ import annotations

#: Current on-disk store format. Stamped into ``state.json`` as
#: ``store_format_version`` on every persist. Legacy stores predating this
#: field are treated as version 0.
#:
#: v2 introduces the derived concept tree (subsystem pages + written prose in
#: place of per-directory module pages). Unlike v1, it is not reachable by the
#: automatic reconcile: only a full re-index rebuilds an old store's pages into
#: it. Its migration is therefore ``REINDEX_RECOMMENDED``, and :func:`stamp`
#: clamps a routine persist just below it so the notice keeps firing until the
#: user actually re-indexes.
STORE_FORMAT_VERSION: int = 2

#: Current parser/extractor schema. Folded into the parse-cache fingerprint in
#: place of the package version. See :mod:`repowise.core.ingestion.parse_cache`.
#:
#: v2: ``RawCall`` gained ``scope_name`` (the C++ ``Ns::f()`` qualifier as
#: written) and ``receiver_call`` (the inner call of a chained expression), and
#: a Rust macro invocation stopped being extracted as a call. A cache written
#: before that carries neither field, so the scoped-call, chained-call and
#: macro fixes would resolve against stale rows instead of firing.
#:
#: v3: a Python import that resolves to no repo file now becomes an
#: ``external:`` node and edge instead of resolving to nothing. The fingerprint
#: change makes ``persist_incremental_edges`` reconcile every file's edges once
#: on the next update, so an existing index does not keep half its Python files
#: without external edges.
#:
#: v4: Python absolute imports resolve only by their full dotted path (no stem
#: guess, no sibling lookup inside a package, never the importer itself), and
#: ``from pkg import a, b`` over submodules no longer edges into
#: ``pkg/__init__.py``. Reconciling once drops the stale edges from old stores.
#:
#: v5: an overload signature (Python ``@overload``, TypeScript function / method
#: overload) is marked ``is_declaration`` so lookups serve the implementation.
#:
#: v6: a member call a bare-call pattern also matched (Java ``obj.m()``, Ruby
#: ``obj.m(x)``) is kept once, with its receiver and ``bare_name_fallback``, so
#: a cached receiver-less copy no longer resolves by bare name beside it. Also,
#: Java, C# and C++ overloads of different arity get their own ids (``notNull#1``,
#: ``notNull#2``) and a C# generic type beside a same-named one gets its arity
#: (``IFoo`1``); the first update re-keys symbols and graph nodes repo-wide. A
#: C++ template function is one ``function`` symbol, no longer also a ``class``.
#:
#: v7: code naming Alembic gets a framework edge to every migration it can
#: load, and a VS Code extension test to the entry its package declares; the
#: first update writes those edges for unchanged files too.
#:
#: v8: a Java call site records its literal arguments' types and its named
#: arguments, whose declared types join them to break a same-arity overload
#: tie. Java, C# and C++ overloads of one arity carry their erased parameter
#: types (``put#1(String)``, ``put#1(int)``), and a method in a Java enum
#: constant's body sits under the constant (``Op::PLUS::apply``), and a Java
#: ``@interface`` and its elements are an ``interface`` and its methods; the first
#: update re-keys those symbols and graph nodes. A Swift ``extension`` is an
#: ``impl``, no longer a ``class``. Swift parses with tree-sitter-swift 0.7, so
#: a type body holding ``#if`` keeps its members.
#: A Python call chained on a method call (``a.get().close()``) carries that
#: inner call.
#: A Dart call chained on a call (``a.b().c()``) carries that inner call too,
#: and a Dart function's signature carries its declared return type.
#: A Kotlin function's signature carries its declared return type.
#: Kotlin overloads and same-named extensions in one scope get their own ids
#: the Java way, an extension's receiver leading its types
#: (``toHexString#0(Byte)``, ``g#0(Int?)``), and a Kotlin trailing lambda counts as an
#: argument.
#: A Kotlin call whose only argument is a trailing lambda (``AppTheme { }``,
#: ``s.forEachSegment { }``) is a call site, with one argument.
#:
#: v9: a Kotlin call chained on a call (``a.b().c()``) is a call site and
#: carries that inner call.
#: A Kotlin ``typealias`` signature carries its target (``typealias Repo =
#: DefaultRepository``), so a call through the alias reaches the repo class.
#: A top-level Kotlin declaration keeps the annotations the grammar splits off
#: it when the last one takes one argument (``@Suppress("unused")``).
#: A top-level Kotlin declaration with two annotations (``@Preview @Composable
#: private fun f()``) that the grammar read as an expression is re-parsed with its
#: leading annotations blanked, so it becomes a symbol again.
#:
#: v10: a JavaScript-family file under a ``tests/`` folder inside a ``src`` tree
#: is not a test by that folder alone, which moves its stored ``is_test``.
#: TypeScript path aliases resolve under solution-style ``tsconfig.json``
#: references, and a JS or TS bare call binds to a same-file function or an
#: import. PL/I ``.inc`` members are not parsed as C++. Apex callouts trace
#: through wrapper methods. Dart dotted receivers, prefixed named constructors
#: and closure parameters typed from the collection call bind. A Ruby gem's
#: public API follows Ruby visibility forms, Ansible playbooks and roles get
#: dependency edges, Scala gets dataflow, and Postgres catalog tables are not
#: workspace consumers.
PARSER_SCHEMA_VERSION: int = 10

#: state.json key holding the store format version that wrote the store.
STORE_FORMAT_VERSION_KEY = "store_format_version"

#: state.json key holding the package ``__version__`` that last wrote the store.
WRITTEN_BY_VERSION_KEY = "written_by_version"

#: state.json key holding the embedding model id the vectors were built with.
EMBEDDING_MODEL_KEY = "embedding_model"

__all__ = [
    "EMBEDDING_MODEL_KEY",
    "PARSER_SCHEMA_VERSION",
    "STORE_FORMAT_VERSION",
    "STORE_FORMAT_VERSION_KEY",
    "WRITTEN_BY_VERSION_KEY",
]

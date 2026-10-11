"""Shared declared-module-name index for lightweight (regex-tier) resolvers.

Languages whose import statements name *modules* rather than files
(Elixir ``alias Foo.Bar``, Clojure ``(:require [foo.bar])``, Haskell
``import Foo.Bar``, Erlang ``mod:fun()``) resolve through one uniform
mechanism: a map from declared module name → defining file(s), built once
per build from

1. a single declaration regex over every file of the language
   (``defmodule Foo.Bar`` / ``(ns foo.bar)`` / ``module Foo.Bar where`` /
   ``-module(foo)``), plus
2. an optional path-convention inverse (``lib/foo/bar.ex`` → ``Foo.Bar``)
   as a fallback for files whose head carries no declaration.

The index is cached on the :class:`~.context.ResolverContext` via the
same ``getattr``/``setattr`` idiom every other lazy per-language index
uses, and iterates ``ctx.sorted_paths`` so its first-match behaviour is
deterministic.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)

# Declarations live near the top of a file in every supported convention;
# capping the read keeps the index build linear in file *count*, not size.
_HEAD_BYTES = 65536


def get_or_build_module_index(
    ctx: ResolverContext,
    *,
    cache_attr: str,
    extensions: tuple[str, ...],
    declaration_re: re.Pattern[str],
    path_to_module: Callable[[str], str | None] | None = None,
) -> dict[str, list[str]]:
    """Return (building on first call) ``{module_name: [paths…]}`` for one language.

    ``declaration_re`` must carry exactly one capture group (the declared
    name) and is applied with ``findall`` to the head of each file whose
    suffix is in ``extensions``. ``path_to_module`` supplies the
    path-convention inverse for files with no declaration.
    """
    cached = getattr(ctx, cache_attr, None)
    if cached is not None:
        return cached

    index: dict[str, list[str]] = {}
    repo = ctx.repo_path
    for path in ctx.sorted_paths:
        if not path.endswith(extensions):
            continue
        declared: list[str] = []
        if repo is not None:
            try:
                with open(repo / path, encoding="utf-8", errors="replace") as f:
                    head = f.read(_HEAD_BYTES)
                declared = declaration_re.findall(head)
            except OSError:
                declared = []
        if not declared and path_to_module is not None:
            inverse = path_to_module(path)
            if inverse:
                declared = [inverse]
        for name in declared:
            index.setdefault(name, []).append(path)

    setattr(ctx, cache_attr, index)
    log.debug("module name index built", cache=cache_attr, modules=len(index))
    return index


def lookup_module(index: dict[str, list[str]], name: str) -> str | None:
    """Deterministic single-file lookup: first path in sorted insertion order."""
    paths = index.get(name)
    return paths[0] if paths else None


def lookup_with_trailing_strip(
    index: dict[str, list[str]], name: str, *, separator: str = "."
) -> str | None:
    """Look up *name*, progressively dropping trailing segments.

    Covers imports of nested modules whose own segment never appears in a
    file head (``Foo.Bar.Baz`` declared inside ``foo/bar.ex``) — the same
    shape as the JVM member-import stripping. Only exact index keys hit;
    a single remaining segment is still allowed (top-level single-segment
    modules are the norm in Elixir libraries).
    """
    candidate = name
    while candidate:
        hit = lookup_module(index, candidate)
        if hit:
            return hit
        if separator not in candidate:
            return None
        candidate = candidate.rsplit(separator, 1)[0]
    return None

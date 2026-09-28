"""Angular convention edges: build entries, router config and decorator metadata.

Angular reaches most of an app through ordinary imports: a route's
``component``, ``loadComponent: () => import(...)`` and ``loadChildren``, an
NgModule's ``declarations`` and a standalone component's ``imports`` all name
an imported class, and the TypeScript parser records static and dynamic
imports alike. What no import says:

* the files the build loads: ``main.ts``, the polyfills, the environment file a
  build swaps in, the Karma config. ``angular.json`` / Nx ``project.json`` name
  them (:func:`..framework_facts.angular_entry_files`), and a file calling
  ``bootstrapApplication`` / ``bootstrapModule`` is one; each is anchored to
  ``framework:angular``;
* a name only written inside router config or decorator metadata
  (``const routes: Routes = [...]`` passed to ``RouterModule.forRoot(routes)``,
  a guard in ``canActivate``). A local one gets a ``framework_binds`` edge from
  the file's module symbol, since no call reaches it; an imported one a file
  edge (usually the import edge already there).

Template selectors (``<app-user>``) are not read: a component a template uses
is declared in an NgModule or a standalone ``imports`` array, both TypeScript
references, so a selector edge would add no reachability the graph lacks.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ..framework_facts import ANGULAR, ANGULAR_WORKSPACE_FILES, angular_entry_files
from ..framework_routes import match_paren
from ..resolvers import ResolverContext
from .base import (
    JS_STRING_LITERAL_RE,
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_ts_var_to_file,
    add_symbol_edge,
    anchor_edge,
    source_text,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    import networkx as nx

_TS_JS = ("typescript", "javascript")

# Where Angular reads names no call reaches: decorator metadata, router config
# (a `Routes` array, `forRoot` / `forChild` / `provideRouter`) and bootstrap.
_SITE_RE = re.compile(
    r"@(?:NgModule|Component|Directive|Pipe)\s*(?P<meta>\()"
    r"|(?P<boot>bootstrapApplication|bootstrapModule)\s*(?P<call>\()"
    r"|(?:forRoot|forChild|provideRouter)\s*(?P<router>\()"
    r"|Routes?(?:\s*\[\s*\])?\s*=\s*(?P<routes>\[)"
)
_SITE_HINTS = ("@NgModule", "@Component", "@Directive", "@Pipe", "bootstrap", "Route")
# Strings and comments in one left-to-right pass, so an apostrophe in
# `// don't` is not read as opening a string, nor `//` inside one as a comment.
_NOISE_RE = re.compile(rf"{JS_STRING_LITERAL_RE.pattern}|//[^\n]*|/\*.*?\*/", re.DOTALL)
# A name standing for a value: not a member (`m.routes`), a spread is one.
_NAME_RE = re.compile(r"(?:(?<=\.\.\.)|(?<![\w$.]))[A-Za-z_$][\w$]*")
# The class a decorator decorates: its own name in its metadata
# (`useExisting: forwardRef(() => SelfComponent)`) is not a use of it.
_DECORATED_CLASS_RE = re.compile(
    r"\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"
)


def _blank(m: re.Match[str]) -> str:
    return "''" if m.group()[0] in "'\"`" else " "


_COLON_RE = re.compile(r"\s*:(?!:)")


def _is_key(span: str, m: re.Match[str]) -> bool:
    """Whether the name *m* is an object key (`{ path: ''`), not a ternary branch."""
    if _COLON_RE.match(span, m.end()) is None:
        return False
    i = m.start() - 1
    while i >= 0 and span[i].isspace():
        i -= 1
    return i < 0 or span[i] in "{,"


def angular_references(text: str) -> Iterator[tuple[str, bool]]:
    """``(name, bootstrap)`` per name Angular metadata, router config or bootstrap writes.

    *bootstrap* marks the names of a ``bootstrapApplication`` /
    ``bootstrapModule`` call, whose file the runtime loads first. A site in a
    comment or a string is none.
    """
    if not any(h in text for h in _SITE_HINTS):
        return
    code = _NOISE_RE.sub(_blank, text)
    for m in _SITE_RE.finditer(code):
        group = next(g for g in ("meta", "call", "router", "routes") if m.group(g) is not None)
        close = match_paren(code, m.start(group))
        if close < 0:
            continue
        span = code[m.start(group) + 1 : close]
        bootstrap = m.group("boot") is not None
        decorated = _DECORATED_CLASS_RE.match(code, close + 1) if group == "meta" else None
        own = decorated.group("name") if decorated else None
        for name in _NAME_RE.finditer(span):
            if name.group() != own and not _is_key(span, name):
                yield name.group(), bootstrap


# A JSON string (kept) or a comment (dropped).
_JSON_COMMENT_RE = re.compile(r'(?P<s>"(?:[^"\\]|\\.)*")|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _entry_files(ctx: ResolverContext, path_set: set[str]) -> set[str]:
    """Every file an Angular workspace config in the repo names as loaded."""
    if ctx.repo_path is None:
        return set()
    out: set[str] = set()
    for path in path_set:
        if path.rpartition("/")[2] not in ANGULAR_WORKSPACE_FILES:
            continue
        try:
            # Both CLIs read JSON with comments, and a BOM is common on Windows.
            text = (ctx.repo_path / path).read_text(encoding="utf-8-sig")
            config = json.loads(_JSON_COMMENT_RE.sub(lambda m: m.group("s") or " ", text))
        except (OSError, ValueError):
            continue
        out.update(angular_entry_files(path, config, path_set))
    return out


def _imports_angular(parsed: Any) -> bool:
    return any(imp.module_path.startswith("@angular/") for imp in parsed.imports)


class _AngularHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        if "angular" in dctx.stack_lower:
            return True
        return any(
            p.file_info.language in _TS_JS and _imports_angular(p)
            for p in dctx.parsed_files.values()
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        count = sum(anchor_edge(graph, ANGULAR, p) for p in sorted(_entry_files(ctx, path_set)))
        source_map = getattr(ctx, "source_map", None) or {}
        for path, parsed in parsed_files.items():
            if parsed.file_info.language not in _TS_JS or not _imports_angular(parsed):
                continue
            refs = list(angular_references(source_text(path, parsed, source_map)))
            if not refs:
                continue
            if any(bootstrap for _, bootstrap in refs):
                count += anchor_edge(graph, ANGULAR, path)
            local = {s.name: s.id for s in parsed.symbols if not s.parent_name}
            module_sym = f"{path}::__module__"
            imported: dict[str, str] | None = None
            for name in {name for name, _ in refs}:
                if name in local:
                    count += add_symbol_edge(graph, module_sym, local[name])
                    continue
                if imported is None:
                    imported = _build_ts_var_to_file(parsed, path, ctx, path_set)
                if name in imported:
                    count += _add_edge_if_new(graph, path, imported[name])
        return count


HANDLERS: list[FrameworkHandler] = [_AngularHandler()]

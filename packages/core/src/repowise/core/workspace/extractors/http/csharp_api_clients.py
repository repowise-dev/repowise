"""Repo-wide resolution of subclasses of configured typed root clients to their base path.

The base path reaches the root constructor through constructor chains and constants
that may sit in other files, so this pre-pass reads every ``.cs`` file as text.
"""

from __future__ import annotations

import dataclasses
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ..strings import CSHARP_SYNTAX, call_arguments, match_paren, resolve_string

# Concatenation is folded here only; CSHARP_SYNTAX stays unchanged for other callers.
CSHARP_APICLIENT_SYNTAX = dataclasses.replace(CSHARP_SYNTAX, concat="+")

_MAX_DEPTH = 4

_CLASS_RE = re.compile(
    r"\b(?:class|struct|record)\s+([A-Za-z_]\w*)\s*(?:<[^{};()]*>)?\s*"
    r"(?:\([^)]*\)\s*)?(?::([^{};]*))?([{;])"
)
_CONST_RE = re.compile(
    r'\b(?:const|static\s+readonly)\s+string\s+([A-Za-z_]\w*)\s*=\s*((?:"(?:[^"\\]|\\.)*"|[^;"])+);'
)
_NAMEOF_RE = re.compile(r"\bnameof\s*\(\s*(?:[A-Za-z_]\w*\s*\.\s*)*([A-Za-z_]\w*)\s*\)")
_NAME_TOKEN_RE = re.compile(r"[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*")
_NAMED_ARG_RE = re.compile(r"^([A-Za-z_]\w*)\s*:(?!:)\s*(.+)$", re.DOTALL)
_WHERE_RE = re.compile(r"\bwhere\b")
_BASE_NAME_RE = re.compile(r"[A-Za-z_][\w.]*")
_INITIALIZER_RE = re.compile(r"\s*:\s*(base|this)\s*\(")


# Strings and char literals are matched so a `//` inside one is not read as a
# comment; only the comment alternatives are blanked. An interpolated string
# allows one level of quoted text inside a hole. Anything unmatched is left as is.
_MASK_RE = re.compile(
    r"""
    (?P<comment>//[^\n]*|/\*.*?\*/)
    |"{3,}.*?"{3,}
    |(?:@\$|\$@|@)"(?:[^"]|"")*"
    |\$"(?:[^"\\{\n]|\\.|\{\{|\{(?:[^{}"\n]|"(?:[^"\\\n]|\\.)*")*\})*"
    |"(?:[^"\\\n]|\\.)*"
    |'(?:[^'\\\n]|\\.[^'\n]*)'
    """,
    re.VERBOSE | re.DOTALL,
)
_NOT_NEWLINE_RE = re.compile(r"[^\n]")


def _mask_comments(text: str) -> str:
    """*text* with comments blanked, offsets and newlines kept."""
    return _MASK_RE.sub(
        lambda m: _NOT_NEWLINE_RE.sub(" ", m.group()) if m.group("comment") else m.group(),
        text,
    )


def _split_top_level(text: str) -> list[str]:
    """Split at commas outside brackets, generics and strings."""
    out: list[str] = []
    depth = 0
    quote = False
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == '"':
                quote = False
        elif ch == '"':
            quote = True
        elif ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i])
            start = i + 1
        i += 1
    out.append(text[start:])
    return [p.strip() for p in out if p.strip()]


@dataclass(frozen=True)
class _Root:
    """A configured root client: its class name and, when qualified, its namespace."""

    name: str
    namespace: str

    @classmethod
    def parse(cls, qualified: str) -> _Root:
        namespace, _, name = qualified.strip().rpartition(".")
        return cls(name, namespace)

    @property
    def qualified(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name

    def in_scope(self, text: str) -> bool:
        """Whether *text* imports or declares the namespace, so a bare name is this root."""
        if not self.namespace:
            return True
        ns = re.escape(self.namespace)
        return bool(
            re.search(rf"^\s*using\s+{ns}\s*;", text, re.MULTILINE)
            or re.search(rf"\bnamespace\s+{ns}\b", text)
        )


@dataclass
class _Param:
    name: str
    default: str | None


@dataclass
class _Ctor:
    params: list[_Param]
    base_args: list[str] | None  # None when the constructor chains with ``: this(...)``
    this_args: list[str] | None = None


@dataclass
class _Class:
    name: str
    rel_path: str
    path: str  # nesting path, ``Outer.Inner``
    scope: tuple[str, ...]  # own path first, then each enclosing type
    start: int
    end: int
    base_simple: str
    is_root_base: bool
    ctors: list[_Ctor] = field(default_factory=list)


@dataclass(frozen=True)
class _Const:
    rhs: str
    scope: tuple[str, ...]


@dataclass(frozen=True)
class ApiClientSpan:
    """A concrete client class: body offsets and its resolved base path."""

    name: str
    scope: tuple[str, ...]
    start: int
    end: int
    base_path: str
    holes: tuple[tuple[int, int], ...] = ()  # nested classes, not part of the client


@dataclass(frozen=True)
class ApiClientIndex:
    """Resolved clients per file, plus the constant table to read their calls."""

    by_file: Mapping[str, tuple[ApiClientSpan, ...]]
    _repo: _Repo

    def constants_for(self, expr: str, scope: tuple[str, ...]) -> dict[str, str]:
        """String constants named in *expr*, as quoted literals for ``resolve_string``."""
        return self._repo.constants_for(expr, scope, {}, 0)


def _param_of(text: str) -> _Param | None:
    head, eq, default = text.partition("=")
    m = re.search(r"([A-Za-z_]\w*)\s*$", head)
    if m is None:
        return None
    return _Param(m.group(1), default.strip() if eq else None)


class _Repo:
    def __init__(self, roots: Sequence[_Root]) -> None:
        self.roots = roots
        self.root_names = frozenset(r.name for r in roots)
        self.classes: list[_Class] = []
        self.by_name: dict[str, list[_Class]] = defaultdict(list)
        self.consts: dict[str, dict[str, list[_Const]]] = defaultdict(lambda: defaultdict(list))

    def add_file(self, rel_path: str, text: str) -> None:
        scoped = {r for r in self.roots if r.in_scope(text)}
        found: list[_Class] = []
        stack: list[_Class] = []
        for m in _CLASS_RE.finditer(text):
            if m.group(3) != "{":
                continue
            open_idx = m.end() - 1
            end = match_paren(text, open_idx, limit=len(text), closer="}")
            if end < 0:
                continue
            while stack and stack[-1].end < open_idx:
                stack.pop()
            parent = stack[-1] if stack else None
            path = f"{parent.path}.{m.group(1)}" if parent else m.group(1)
            bases = _WHERE_RE.split(m.group(2) or "")[0]
            first = _split_top_level(bases)[:1]
            raw = ""
            if first:
                name_m = _BASE_NAME_RE.match(first[0])
                raw = name_m.group() if name_m else ""
            simple = raw.rsplit(".", 1)[-1]
            cls = _Class(
                name=m.group(1),
                rel_path=rel_path,
                path=path,
                scope=(path, *(parent.scope if parent else ())),
                start=open_idx,
                end=end,
                base_simple=simple,
                is_root_base=any(
                    r.name == simple and (raw == r.qualified or (raw == r.name and r in scoped))
                    for r in self.roots
                ),
            )
            found.append(cls)
            stack.append(cls)
        for cls in found:
            self._add_ctors(cls, text, found)
            self.classes.append(cls)
            self.by_name[cls.name].append(cls)
        for m in _CONST_RE.finditer(text):
            owner = max(
                (c for c in found if c.start < m.start() < c.end),
                key=lambda c: c.start,
                default=None,
            )
            if owner is not None:
                self.consts[owner.path][m.group(1)].append(_Const(m.group(2).strip(), owner.scope))

    @staticmethod
    def _add_ctors(cls: _Class, text: str, found: list[_Class]) -> None:
        nested = [c for c in found if cls.start < c.start and c.end < cls.end]
        name_re = re.compile(rf"(?<![\w.]){re.escape(cls.name)}\s*\(")
        for m in name_re.finditer(text, cls.start, cls.end):
            if any(c.start < m.start() < c.end for c in nested):
                continue
            if text[max(0, m.start() - 8) : m.start()].rstrip().endswith("new"):
                continue
            close = match_paren(text, m.end() - 1)
            if close < 0:
                continue
            init = _INITIALIZER_RE.match(text, close + 1)
            tail = text[close + 1 :].lstrip()
            if init is None and not tail.startswith(("{", "=>")):
                continue  # a call or a method that shares the class name
            params = [
                p
                for p in (_param_of(t) for t in _split_top_level(text[m.end() : close]))
                if p is not None
            ]
            base_args: list[str] | None = []
            this_args: list[str] | None = None
            if init is not None:
                init_args = call_arguments(text, init.end() - 1)
                if init_args is None:
                    continue
                if init.group(1) == "this":
                    base_args, this_args = None, init_args
                else:
                    base_args = init_args
            cls.ctors.append(_Ctor(params, base_args, this_args))

    def const_value(self, ref: str, scope: tuple[str, ...], depth: int) -> str | None:
        ref = re.sub(r"\s+", "", ref)
        type_ref, _, name = ref.rpartition(".")
        entries: list[_Const] = []
        if not type_ref:
            for s in scope:
                if name in self.consts.get(s, {}):
                    entries = self.consts[s][name]
                    break
        else:
            keys = [f"{s}.{type_ref}" for s in scope] + [type_ref]
            hit = next((k for k in keys if name in self.consts.get(k, {})), None)
            if hit is not None:
                entries = self.consts[hit][name]
            else:
                for t, names in self.consts.items():
                    if t.endswith("." + type_ref) and name in names:
                        entries.extend(names[name])
        values = {self.eval(e.rhs, e.scope, {}, depth + 1) for e in entries}
        if len(values) != 1 or None in values:
            return None
        return values.pop()

    def constants_for(
        self, expr: str, scope: tuple[str, ...], env: dict[str, str | None], depth: int
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in _NAME_TOKEN_RE.finditer(expr):
            key = m.group()
            if key in out:
                continue
            val = env[key] if key in env else self.const_value(key, scope, depth)
            if val is not None and '"' not in val:
                out[key] = f'"{val}"'
        return out

    def eval(
        self, expr: str, scope: tuple[str, ...], env: dict[str, str | None], depth: int
    ) -> str | None:
        """*expr* as a fully constant string, else ``None``."""
        if depth > _MAX_DEPTH:
            return None
        text = _NAMEOF_RE.sub(lambda m: f'"{m.group(1)}"', expr)
        consts = self.constants_for(text, scope, env, depth)
        url = resolve_string(text, CSHARP_APICLIENT_SYNTAX, consts)
        return None if url is None or "${" in url else url

    def _bind(
        self, callee: _Class, ctor: _Ctor, args: list[str], caller: _Class, env: dict
    ) -> dict[str, str | None] | None:
        pos: list[str] = []
        named: dict[str, str] = {}
        for a in args:
            m = _NAMED_ARG_RE.match(a)
            if m:
                named[m.group(1)] = m.group(2)
            else:
                pos.append(a)
        if len(pos) > len(ctor.params) or not set(named) <= {p.name for p in ctor.params}:
            return None
        bound: dict[str, str | None] = {}
        for i, p in enumerate(ctor.params):
            if i < len(pos):
                bound[p.name] = self.eval(pos[i], caller.scope, env, 0)
            elif p.name in named:
                bound[p.name] = self.eval(named[p.name], caller.scope, env, 0)
            elif p.default is not None:
                bound[p.name] = self.eval(p.default, callee.scope, {}, 0)
            else:
                return None
        return bound

    def _this_targets(
        self, cls: _Class, ctor: _Ctor, env: dict
    ) -> list[tuple[_Ctor, dict[str, str | None]]]:
        """Sibling constructors *ctor* delegates to, with their bound parameters."""
        out = []
        for sib in cls.ctors:
            if sib is not ctor and ctor.this_args is not None:
                bound = self._bind(cls, sib, ctor.this_args, cls, env)
                if bound is not None:
                    out.append((sib, bound))
        return out

    def ctor_path(
        self, cls: _Class, ctor: _Ctor, env: dict, depth: int, seen: frozenset[int], hops: int = 0
    ) -> str | None:
        """The root base path *ctor* reaches, following ``: this(...)`` inside *cls*."""
        if ctor.base_args is not None:
            return self.base_path(cls, ctor.base_args, env, depth, seen)
        if hops > _MAX_DEPTH:
            return None
        results = {
            self.ctor_path(cls, sib, bound, depth, seen, hops + 1)
            for sib, bound in self._this_targets(cls, ctor, env)
        }
        return results.pop() if len(results) == 1 and None not in results else None

    def _root_path_index(self, cls: _Class, n_args: int) -> int | None:
        """Position of the path parameter in the root's own constructor, when it is in the repo."""
        roots = self.by_name.get(cls.base_simple, [])
        if len(roots) != 1:
            return None
        for ctor in roots[0].ctors:
            if len(ctor.params) >= n_args:
                for i, p in enumerate(ctor.params):
                    if "path" in p.name.lower():
                        return i
        return None

    def _root_path(self, cls: _Class, args: list[str], env: dict) -> str | None:
        """The base path *cls* hands the root constructor: ``""`` for no arguments."""
        if not args:
            return ""
        named: dict[str, str] = {}
        pos: list[str] = []
        for a in args:
            m = _NAMED_ARG_RE.match(a)
            if m:
                named[m.group(1)] = m.group(2)
            else:
                pos.append(a)
        explicit = next((v for k, v in named.items() if "path" in k.lower()), None)
        if explicit is None:
            idx = self._root_path_index(cls, len(args))
            if idx is not None and idx < len(pos):
                explicit = pos[idx]
        if explicit is not None:
            return self.eval(explicit, cls.scope, env, 0)
        for arg in pos:
            value = self.eval(arg, cls.scope, env, 0)
            if value is not None and "://" not in value and not re.search(r"\s", value):
                return value
        return None

    def base_path(
        self, cls: _Class, args: list[str], env: dict, depth: int, seen: frozenset[int]
    ) -> str | None:
        """The root base path when *cls* calls its base with *args*."""
        if depth > _MAX_DEPTH or id(cls) in seen:
            return None
        if cls.is_root_base:
            return self._root_path(cls, args, env)
        bases = self.by_name.get(cls.base_simple, [])
        if cls.base_simple in self.root_names or len(bases) != 1:
            return None
        base = bases[0]
        results: set[str | None] = set()
        for ctor in base.ctors:
            bound = self._bind(base, ctor, args, cls, env)
            if bound is not None:
                results.add(self.ctor_path(base, ctor, bound, depth + 1, seen | {id(cls)}))
        return results.pop() if len(results) == 1 and None not in results else None

    def build(self) -> ApiClientIndex:
        by_file: dict[str, list[ApiClientSpan]] = defaultdict(list)
        for cls in self.classes:
            if len(self.by_name[cls.name]) != 1:
                continue
            paths: set[str | None] = set()
            envs = {id(c): {p.name: None for p in c.params} for c in cls.ctors}
            # A constructor another one delegates to gets its path from that caller.
            delegated = {
                id(sib) for c in cls.ctors for sib, _ in self._this_targets(cls, c, envs[id(c)])
            }
            for ctor in cls.ctors:
                if id(ctor) not in delegated:
                    paths.add(self.ctor_path(cls, ctor, envs[id(ctor)], 0, frozenset()))
            if len(paths) == 1 and None not in paths:
                by_file[cls.rel_path].append(
                    ApiClientSpan(
                        cls.name,
                        cls.scope,
                        cls.start,
                        cls.end,
                        paths.pop() or "",
                        tuple(
                            (c.start, c.end)
                            for c in self.classes
                            if c.rel_path == cls.rel_path and cls.start < c.start < c.end < cls.end
                        ),
                    )
                )
        return ApiClientIndex({k: tuple(v) for k, v in by_file.items()}, self)


def collect_api_clients(
    files: Iterable[tuple[str, str, str]], bases: Sequence[str] = ()
) -> ApiClientIndex | None:
    """Resolve every subclass of a *bases* root client in *files*; ``None`` when there are none.

    Each base is a qualified class name (``Acme.Http.ApiClient``) or a bare one.
    """
    roots = [_Root.parse(b) for b in bases if b.strip()]
    if not roots:
        return None
    cs = [(rel, content) for rel, suffix, content in files if suffix == ".cs"]
    needles = {r.namespace or r.name for r in roots}
    if not any(n in content for _, content in cs for n in needles):
        return None
    repo = _Repo(roots)
    for rel, content in cs:
        repo.add_file(rel, _mask_comments(content))
    index = repo.build()
    return index if index.by_file else None

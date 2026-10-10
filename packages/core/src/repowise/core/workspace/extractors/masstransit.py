"""MassTransit message contracts for C# and VB.NET.

A MassTransit message is routed by its .NET type, so the contract id is the
message type's ``namespace:name`` (MassTransit's default entity name), not a
string topic. A type is only a message when it is declared in a contract
project (``contracts.contract_project_pattern``): that index is the gate that
keeps ``.Send(x)`` on a socket or a mail client out of the results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.fs_walk import iter_glob
from repowise.core.ingestion.resolvers.dotnet.msbuild import (
    find_csproj_files,
    path_has_dotnet_scan_skip_dir,
)
from repowise.core.ingestion.resolvers.dotnet.namespace_map import (
    declared_namespaces,
    scan_type_declarations,
)

from ..config import DEFAULT_CONTRACT_PROJECT_PATTERN
from .base import line_at, select_files
from .langs import CSHARP, VBNET

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from repowise.core.workspace.contracts import Contract

    from .base import SourceFile

_TEST_DIR_RE = re.compile(
    r"^.+\.(?:tests?|unittests?|integrationtests?|specs?|testing|testhelpers?)$", re.IGNORECASE
)

_TIER_DECLARED = 0.8
_TIER_UNIQUE_NAME = 0.6
_TIER_DATAFLOW = 0.7


# ---------------------------------------------------------------------------
# Message type index
# ---------------------------------------------------------------------------


@dataclass
class MessageTypeIndex:
    """Every top-level type declared in a contract project, workspace-wide."""

    fqns: set[str] = field(default_factory=set)
    by_short: dict[str, set[str]] = field(default_factory=dict)

    def add(self, fqn: str) -> None:
        self.fqns.add(fqn)
        self.by_short.setdefault(fqn.rsplit(".", 1)[-1], set()).add(fqn)


def _in_test_project(rel_path: str) -> bool:
    return any(_TEST_DIR_RE.match(part) for part in rel_path.split("/")[:-1])


def build_message_type_index(
    repo_paths: Mapping[str, Path],
    exclude: Callable[[str], bool] | None = None,
    contract_project_pattern: str = DEFAULT_CONTRACT_PROJECT_PATTERN,
) -> MessageTypeIndex:
    """Collect the declared types of every contract project in *repo_paths*.

    A project is a contract project when one directory name on its path matches
    *contract_project_pattern* in full, ignoring case.
    """
    index = MessageTypeIndex()
    dir_re = re.compile(contract_project_pattern, re.IGNORECASE)
    for repo in repo_paths.values():
        seen: set[Path] = set()
        for csproj in find_csproj_files(repo):
            try:
                rel_dir = csproj.parent.relative_to(repo).parts
            except ValueError:
                continue
            if not any(dir_re.fullmatch(part) for part in rel_dir):
                continue
            for cs in iter_glob(csproj.parent, "*.cs"):
                if cs in seen or path_has_dotnet_scan_skip_dir(cs, repo):
                    continue
                seen.add(cs)
                rel = cs.relative_to(repo).as_posix()
                if _in_test_project(rel) or (exclude is not None and exclude(rel)):
                    continue
                try:
                    text = cs.read_bytes().decode("utf-8-sig", errors="replace")
                except OSError:
                    continue
                for decl in scan_type_declarations(text):
                    if decl.namespace and decl.qualified == decl.name and not decl.arity:
                        index.add(decl.fqn)
    return index


# ---------------------------------------------------------------------------
# Comment masking
# ---------------------------------------------------------------------------

_CS_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/"
    r'|\$?@\$?"(?:[^"]|"")*"|\$?"(?:\\.|[^"\\\n])*"'
    r"|\x27(?:\\.[^\x27\n]*?|[^\\\x27\n])\x27",
    re.DOTALL,
)
_VB_TOKEN = re.compile(
    r'"(?:[^"\n]|"")*"|\x27[^\n]*|^[ \t]*REM\b[^\n]*',
    re.IGNORECASE | re.MULTILINE,
)
_NOT_NEWLINE = re.compile(r"[^\n]")


def _mask_non_code(text: str, vb: bool) -> str:
    """Blank out comments and string literals, keeping every offset and newline."""
    return (_VB_TOKEN if vb else _CS_TOKEN).sub(lambda m: _NOT_NEWLINE.sub(" ", m.group()), text)


# ---------------------------------------------------------------------------
# Per-file context and name resolution
# ---------------------------------------------------------------------------

_CS_USING = re.compile(r"^[ \t]*(?:global\s+)?using\s+(?:(\w+)\s*=\s*)?([\w.]+)\s*;", re.MULTILINE)
_VB_IMPORTS = re.compile(
    r"^[ \t]*Imports\s+(?:(\w+)\s*=\s*)?([\w.]+)", re.IGNORECASE | re.MULTILINE
)
_VB_NAMESPACE = re.compile(r"^[ \t]*Namespace\s+([\w.]+)", re.IGNORECASE | re.MULTILINE)
_GLOBAL_PREFIX = re.compile(r"^global(?:::|\.)", re.IGNORECASE)
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


@dataclass
class _FileContext:
    namespaces: list[str]
    usings: list[str]
    aliases: dict[str, str]


def _file_context(masked: str, vb: bool) -> _FileContext:
    usings: list[str] = []
    aliases: dict[str, str] = {}
    for m in (_VB_IMPORTS if vb else _CS_USING).finditer(masked):
        alias, target = m.groups()
        if alias is None:
            usings.append(target)
        elif not vb:
            aliases[alias] = target
    if vb:
        namespaces = [m.group(1) for m in _VB_NAMESPACE.finditer(masked)]
    else:
        namespaces = declared_namespaces(masked)
    return _FileContext(namespaces, usings, aliases)


@dataclass(frozen=True)
class _Resolved:
    fqn: str
    confidence: float
    resolution: str


class _Resolver:
    def __init__(self, index: MessageTypeIndex) -> None:
        self._fqns = {f.lower(): f for f in index.fqns}
        self._by_short = {
            short.lower(): {f.lower() for f in fqns} for short, fqns in index.by_short.items()
        }

    def resolve(
        self, written: str, ctx: _FileContext, local_names: Callable[[], set[str]]
    ) -> _Resolved | str:
        """A resolved type, or ``"ambiguous"`` / ``"unresolved"``.

        A bare name that no using reaches resolves by its short name only when
        the repo declares no type of that name outside the contract projects.
        """
        name = _GLOBAL_PREFIX.sub("", written)
        candidates = [name]
        for ns in ctx.namespaces:
            parts = ns.split(".")
            candidates += [".".join(parts[:i]) + "." + name for i in range(len(parts), 0, -1)]
        candidates += [f"{u}.{name}" for u in ctx.usings]
        head, _, rest = name.partition(".")
        if head in ctx.aliases:
            candidates.append(ctx.aliases[head] + (f".{rest}" if rest else ""))

        hits = {c.lower() for c in candidates} & self._fqns.keys()
        if len(hits) == 1:
            label = "qualified" if "." in name else "using"
            return _Resolved(self._fqns[next(iter(hits))], _TIER_DECLARED, label)
        if len(hits) > 1:
            return "ambiguous"
        if "." in name:
            return "unresolved"
        short = name.rsplit(".", 1)[-1].lower()
        shorts = self._by_short.get(short, set())
        if shorts and short in local_names():
            return "unresolved"
        if len(shorts) == 1:
            return _Resolved(self._fqns[next(iter(shorts))], _TIER_UNIQUE_NAME, "unique_name")
        return "ambiguous" if shorts else "unresolved"


# ---------------------------------------------------------------------------
# Consumers
# ---------------------------------------------------------------------------

_CS_CONSUMER = re.compile(
    r"\bIConsumer\s*<\s*((?:global::)?[\w.]+(?:\s*<\s*(?:global::)?[\w.]+\s*>)?)\s*>"
)
_VB_CONSUMER = re.compile(
    r"\bIConsumer\s*\(\s*Of\s+((?:Global\.)?[\w.]+(?:\s*\(\s*Of\s+(?:Global\.)?[\w.]+\s*\))?)\s*\)",
    re.IGNORECASE,
)
_CS_CONSUME_METHOD = re.compile(r"\bConsume\s*\(\s*ConsumeContext\s*<\s*(.+?)\s*>\s+\w+")
_VB_CONSUME_METHOD = re.compile(
    r"\bConsume\s*\(\s*(?:ByVal\s+)?\w+\s+As\s+ConsumeContext\s*\(\s*Of\s+(.+?)\s*\)\s*\)",
    re.IGNORECASE,
)
_CS_STATE_MACHINE = re.compile(r"\bMassTransitStateMachine\s*<")
_VB_STATE_MACHINE = re.compile(r"\bMassTransitStateMachine\s*\(\s*Of\b", re.IGNORECASE)
_CS_SAGA_EVENT = re.compile(r"\bEvent\s*<\s*((?:global::)?[\w.]+)\s*>\s+\w+")
_VB_SAGA_EVENT = re.compile(
    r"\bAs\s+\[?Event\]?\s*\(\s*Of\s+((?:Global\.)?[\w.]+)\s*\)", re.IGNORECASE
)
_FAULT = re.compile(r"Fault\s*(?:<|\(\s*Of\s+)\s*(.+?)\s*[>)]$", re.IGNORECASE)


def _norm_generic(text: str) -> str:
    text = re.sub(r"\(\s*Of\s+", "<", text, flags=re.IGNORECASE)
    text = text.replace(")", ">")
    return re.sub(r"\s+|global(?:::|\.)", "", text, flags=re.IGNORECASE).lower()


# ---------------------------------------------------------------------------
# Producers
# ---------------------------------------------------------------------------

_BASE_CALLS = (
    "Publish",
    "Send",
    "SchedulePublish",
    "ScheduleSend",
    "ScheduleRecurringSend",
    "Respond",
    "RespondAsync",
    "CreateRequestClient",
)
_ARG_CALLS = frozenset(
    {"Publish", "Send", "SchedulePublish", "ScheduleSend", "ScheduleRecurringSend"}
    | {"Respond", "RespondAsync"}
)
# Arguments before the message in the non-generic form: (when), (uri, when), (uri, schedule).
_LEADING_ARGS = {"SchedulePublish": 1, "ScheduleSend": 2, "ScheduleRecurringSend": 2}
_CALL_KIND = {
    "Publish": "publish",
    "Send": "send",
    "Respond": "respond",
    "RespondAsync": "respond",
    "CreateRequestClient": "request",
}
_RECEIVER = re.compile(
    r"(?<![\w.])((?:[A-Za-z_]\w*(?:\([^()]*\))?\s*\??\.\s*)*[A-Za-z_]\w*(?:\([^()]*\))?)\s*$"
)
_NOT_AN_ARG = frozenset(
    {"new", "nothing", "null", "me", "this", "default", "typeof", "function", "sub"}
)
_NOT_A_TYPE = frozenset(
    {"return", "await", "new", "throw", "else", "in", "is", "as", "using", "case", "yield", "ref"}
)
_VB_GENERIC_OPEN = re.compile(r"\(\s*Of\s+", re.IGNORECASE)
_VB_SCOPE_END = re.compile(r"^[ \t]*End\s+(?:Sub|Function|Property|Operator)\b", re.I | re.M)


def _call_pattern(wrappers: Mapping[str, list[_Wrapper]]) -> re.Pattern[str]:
    names = _BASE_CALLS + tuple(sorted(wrappers))
    return re.compile(r"\.\s*(" + "|".join(map(re.escape, names)) + r")\b")


def _skip_ws(text: str, i: int, vb: bool) -> int:
    while i < len(text):
        continuation = vb and text[i] == "_" and text[i + 1 : i + 2].isspace()
        if not (text[i].isspace() or continuation):
            break
        i += 1
    return i


def _balanced(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket closing the one at *i*, or -1."""
    depth = 0
    for j in range(i, min(len(text), i + 300)):
        ch = text[j]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
        elif ch in ";{}":
            return -1
    return -1


def _split_args(args: str) -> list[str]:
    """Split a generic or parameter list at its top-level commas."""
    depth = 0
    start = 0
    parts: list[str] = []
    for j, ch in enumerate(args):
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(args[start:j].strip())
            start = j + 1
    parts.append(args[start:].strip())
    return parts


def _last_type_arg(args: str) -> str | None:
    last = _split_args(args)[-1]
    return last if _DOTTED.fullmatch(_GLOBAL_PREFIX.sub("", last)) else None


def _generic_at(text: str, i: int, vb: bool) -> tuple[list[str], int] | None:
    """Type arguments of the generic list opening at *i*, and the index past it."""
    if vb:
        g = _VB_GENERIC_OPEN.match(text, i)
        end = _balanced(text, i, "(", ")") if g else -1
        return (_split_args(text[g.end() : end - 1]), end) if g and end >= 0 else None
    if text[i : i + 1] != "<":
        return None
    end = _balanced(text, i, "<", ">")
    return (_split_args(text[i + 1 : end - 1]), end) if end >= 0 else None


def _declared_type(masked: str, call_start: int, ident: str, vb: bool) -> str | None:
    """Type of *ident* from its nearest declaration above *call_start*."""
    name = re.escape(ident)
    if vb:
        scope_end = 0
        for m in _VB_SCOPE_END.finditer(masked, 0, call_start):
            scope_end = m.end()
        window_start = scope_end
        patterns = (
            rf"(?<![\w.]){name}\s+As\s+(?:New\s+)?([A-Za-z_][\w.]*)",
            rf"\bDim\s+{name}\s*=\s*New\s+([A-Za-z_][\w.]*)",
        )
        flags = re.IGNORECASE
    else:
        window_start = 0
        patterns = (rf"(?<![\w.])(var|[A-Za-z_][\w.]*)\??\s+{name}\s*(?=[=;,)])",)
        flags = 0
    best: tuple[int, str | None] = (-1, None)
    for pat in patterns:
        for m in re.finditer(pat, masked[window_start:call_start], flags):
            typ = m.group(1)
            if typ.lower() in _NOT_A_TYPE:
                continue
            if typ == "var":
                init = re.match(r"\s*=\s*new\s+([A-Za-z_][\w.]*)", masked[window_start + m.end() :])
                typ = init.group(1) if init else None
            if typ is not None and m.start() >= best[0]:
                best = (m.start(), typ)
    return best[1]


@dataclass(frozen=True)
class _ProducerCall:
    start: int
    method: str
    receiver: str
    written: str
    dataflow: bool
    base: str


def _after_first_arg(text: str, i: int) -> int:
    """Index just past the first top-level argument starting at *i*, or -1."""
    depth = 0
    for j in range(i, min(len(text), i + 200)):
        ch = text[j]
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth -= 1
            if depth < 0:
                return -1
        elif ch == "," and depth == 0:
            return j + 1
        elif ch in ";{}":
            return -1
    return -1


def _wrapper_call_ok(masked: str, start: int, vb: bool, w: _Wrapper) -> bool | None:
    """Whether the receiver can be a bus; None for a static-form call, True/False otherwise."""
    r = _RECEIVER.search(masked[max(0, start - 120) : start])
    recv = r.group(1) if r else ""
    if not re.fullmatch(r"[A-Za-z_]\w*", recv):
        return True
    if recv.lower() == w.owner:
        return None
    typ = _declared_type(masked, start, recv, vb)
    # A derived bus type is still named like one; a logger or container is not.
    return typ is None or bool(_BUS_LIKE.search(typ.rsplit(".", 1)[-1]))


def _find_producers(
    masked: str, vb: bool, call_re: re.Pattern[str], wrappers: Mapping[str, list[_Wrapper]]
) -> list[_ProducerCall]:
    calls: list[_ProducerCall] = []
    new_re = re.compile(r"new\s+([A-Za-z_][\w.]*)", re.IGNORECASE if vb else 0)
    for m in call_re.finditer(masked):
        overloads = wrappers.get(m.group(1), [])
        w = overloads[0] if overloads else None
        base = w.base if w else m.group(1)
        static = False
        if w:
            ok = _wrapper_call_ok(masked, m.start(), vb, w)
            if ok is False:
                continue
            static = ok is None
        i = _skip_ws(masked, m.end(), vb)
        written: str | None = None
        dataflow = False
        if w:
            g = _generic_at(masked, i, vb)
            if g:
                picks = {
                    o.tindex for o in overloads if o.arity == len(g[0]) and o.tindex is not None
                }
                if len(picks) != 1:
                    continue
                written = _written_type(g[0][picks.pop()])
                i = _skip_ws(masked, g[1], vb)
        elif vb:
            g = _VB_GENERIC_OPEN.match(masked, i)
            if g:
                end = _balanced(masked, i, "(", ")")
                if end < 0:
                    continue
                written = _last_type_arg(masked[g.end() : end - 1])
                i = _skip_ws(masked, end, vb)
        elif masked[i : i + 1] == "<":
            end = _balanced(masked, i, "<", ">")
            if end < 0:
                continue
            written = _last_type_arg(masked[i + 1 : end - 1])
            i = _skip_ws(masked, end, vb)
        if masked[i : i + 1] != "(":
            continue
        i = _skip_ws(masked, i + 1, vb)
        if static:
            i = _after_first_arg(masked, i)
            if i < 0:
                continue
            i = _skip_ws(masked, i, vb)
        if written is None and base in _ARG_CALLS:
            for _ in range(_LEADING_ARGS.get(base, 0)):
                i = _after_first_arg(masked, i)
                if i < 0:
                    break
                i = _skip_ws(masked, i, vb)
            if i < 0:
                continue
            n = new_re.match(masked, i)
            if n and n.group(1).lower() != "with":
                written = n.group(1)
            elif not n:
                a = re.match(r"([A-Za-z_]\w*)\s*(?=[,)])", masked[i : i + 80])
                if a and a.group(1).lower() not in _NOT_AN_ARG:
                    written = _declared_type(masked, m.start(), a.group(1), vb)
                    dataflow = True
        if not written:
            continue
        window = masked[max(0, m.start() - 120) : m.start()]
        r = _RECEIVER.search(window)
        calls.append(
            _ProducerCall(m.start(), m.group(1), r.group(1) if r else "", written, dataflow, base)
        )
    return calls


# ---------------------------------------------------------------------------
# Generic consumers, generic registration helpers and bus wrapper methods
# ---------------------------------------------------------------------------

_CS_CLASS_HEAD = re.compile(r"\b(?:class|record)\s+(\w+)")
_VB_CLASS_HEAD = re.compile(
    r"^[ \t]*(?:(?:Public|Private|Friend|Protected|Partial|MustInherit|NotInheritable|Shadows)"
    r"[ \t]+)*Class[ \t]+(\w+)",
    re.IGNORECASE | re.MULTILINE,
)
_CS_BASE_END = re.compile(r"\{|;|\bwhere\s+\w+\s*:")
_VB_BASE_LINE = re.compile(
    r"(?:[ \t]*\r?\n)*[ \t]*(?:Inherits|Implements)[ \t]+([^\n]*)(?:\n|$)", re.IGNORECASE
)
_TYPE_PARAM = re.compile(r"(?:(?:in|out)\s+)?(\w+)", re.IGNORECASE)
_BASE_REF = re.compile(r"(?:global(?:::|\.))?(?:\w+\.)*(\w+)\s*", re.IGNORECASE)
_REGISTER_CS = re.compile(r"\b(?:AddConsumer|Consumer)\s*(?=<)")
_REGISTER_VB = re.compile(r"\b(?:AddConsumer|Consumer)\s*(?=\(\s*Of\b)", re.IGNORECASE)
_CS_GENERIC_METHOD = re.compile(r"\b(\w+)\s*(?=<\s*\w+(?:\s*,\s*\w+)*\s*>\s*\()")
_VB_GENERIC_METHOD = re.compile(r"\b(?:Sub|Function)\s+(\w+)\s*(?=\(\s*Of\b)", re.IGNORECASE)
_CS_WHERE = re.compile(r"\s*\bwhere\s+\w+\s*:[^{=;]*")
_VB_METHOD_END = re.compile(r"^[ \t]*End\s+(?:Sub|Function)\b", re.IGNORECASE | re.MULTILINE)
_CS_EXTENSION = re.compile(r"\b(\w+)\s*(?:<([^<>()]*)>)?\s*\(\s*this\s+")
_VB_EXTENSION = re.compile(
    r"<\s*(?:[\w.]+\.)?Extension\s*(?:\(\s*\))?\s*>\s*(?:_\s*)?"
    r"(?:\w+\s+)*?(?:Sub|Function)\s+(\w+)",
    re.IGNORECASE,
)
_VB_PARAM_NAME = re.compile(r"(?:(?:ByVal|ByRef|Optional|ParamArray)\s+)*(\w+)", re.IGNORECASE)
_CS_PARAM_NAME = re.compile(r"(\w+)\s*(?:=.*)?$", re.DOTALL)
_MAX_BODY = 4000
_BUS_TYPES = frozenset(
    {
        "ibus",
        "ibuscontrol",
        "ipublishendpoint",
        "isendendpoint",
        "isendendpointprovider",
        "consumecontext",
        "sagaconsumecontext",
        "behaviorcontext",
    }
)
_BUS_LIKE = re.compile(r"bus|endpoint|context|outbox", re.IGNORECASE)
_CONFIGURATOR_TYPE = re.compile(r"I\w*(?:Registration|Bus|Endpoint)\w*Configurator", re.IGNORECASE)
_CS_PARAM_TYPE = re.compile(
    r"(?:(?:ref|in|out|params)\s+)?(?:global::)?([\w.]+)\s*(?:<.*>)?\??\s+\w+", re.DOTALL
)
_VB_PARAM_TYPE = re.compile(r"\bAs\s+(?:Global\.)?([\w.]+)", re.IGNORECASE)
_CS_OWNER = re.compile(r"\b(?:class|struct)\s+(\w+)")
_VB_OWNER = re.compile(
    r"^[ \t]*(?:\w+[ \t]+)*?(?:Module|Class)[ \t]+(\w+)", re.IGNORECASE | re.MULTILINE
)
_CS_BUS_CALL = re.compile(
    r"\b(\w+)\s*\.\s*(Publish|Send)\s*(?:<\s*[\w.]+\s*>\s*)?\(\s*(\w+)\s*(?=[,)])"
)
_VB_BUS_CALL = re.compile(
    r"\b(\w+)\s*\.\s*(Publish|Send)\s*(?:\(\s*Of\s+[\w.]+\s*\)\s*)?\(\s*(\w+)\s*(?=[,)])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Wrapper:
    """An extension method that publishes or sends its message parameter."""

    base: str
    owner: str
    arity: int
    tindex: int | None


@dataclass(frozen=True)
class _OpenConsumer:
    """Which type parameter of an open-generic class its ``IConsumer`` consumes."""

    param: int
    fault: bool


@dataclass(frozen=True)
class _ClassDecl:
    name: str
    tparams: tuple[str, ...]
    bases: tuple[tuple[str, tuple[str, ...]], ...]
    offset: int


@dataclass
class _RepoScan:
    """Per-repo facts that a call site alone cannot show."""

    wrappers: dict[str, list[_Wrapper]] = field(default_factory=dict)
    open_names: set[str] = field(default_factory=set)
    helper_names: set[str] = field(default_factory=set)
    open_consumers: dict[tuple[str, int], _OpenConsumer] = field(default_factory=dict)
    # (name, arity) -> [(type parameter index, fault)] registered by the helper body
    helpers: dict[tuple[str, int], list[tuple[int, bool]]] = field(default_factory=dict)
    helper_spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


def _base_ref(base: str, vb: bool) -> tuple[str, tuple[str, ...]] | None:
    m = _BASE_REF.match(base)
    if not m:
        return None
    g = _generic_at(base, m.end(), vb)
    return m.group(1), tuple(g[0]) if g else ()


def _tparam_names(args: list[str]) -> tuple[str, ...]:
    return tuple(m.group(1) for a in args if (m := _TYPE_PARAM.match(a)))


def _vb_bases(masked: str, pos: int) -> list[str]:
    nl = masked.find("\n", pos)
    j = nl + 1 if nl >= 0 else len(masked)
    out: list[str] = []
    while (m := _VB_BASE_LINE.match(masked, j)) and m.end() > j:
        out += _split_args(m.group(1))
        j = m.end()
    return out


def _class_decls(masked: str, vb: bool, generic_only: bool) -> Iterator[_ClassDecl]:
    for m in (_VB_CLASS_HEAD if vb else _CS_CLASS_HEAD).finditer(masked):
        i = _skip_ws(masked, m.end(), vb)
        g = _generic_at(masked, i, vb)
        tparams = _tparam_names(g[0]) if g else ()
        if generic_only and not tparams:
            continue
        if g:
            i = _skip_ws(masked, g[1], vb)
        if vb:
            raw = _vb_bases(masked, g[1] if g else m.end())
        else:
            if masked[i : i + 1] == "(":
                end = _balanced(masked, i, "(", ")")
                if end < 0:
                    continue
                i = _skip_ws(masked, end, vb)
            if masked[i : i + 1] != ":":
                continue
            seg = masked[i + 1 : i + 501]
            stop = _CS_BASE_END.search(seg)
            if not stop:
                continue
            raw = _split_args(seg[: stop.start()])
        bases = tuple(r for b in raw if (r := _base_ref(b, vb)) is not None)
        if bases:
            yield _ClassDecl(m.group(1), tparams, bases, m.start())


def _consumed_arg(
    name: str, args: tuple[str, ...], known: Mapping[tuple[str, int], _OpenConsumer]
) -> tuple[str, bool] | None:
    """The message type written for a consumer base or interface, and whether it is a fault."""
    if name.lower() == "iconsumer":
        entry = _OpenConsumer(0, False) if len(args) == 1 else None
    else:
        entry = known.get((name.lower(), len(args)))
    if entry is None:
        return None
    arg = args[entry.param]
    fault = _FAULT.match(arg)
    return (fault.group(1), True) if fault else (arg, entry.fault)


def _open_consumers(decls: list[_ClassDecl]) -> dict[tuple[str, int], _OpenConsumer]:
    """Generic classes that implement ``IConsumer`` of one of their type parameters."""
    known: dict[tuple[str, int], _OpenConsumer | None] = {}
    for _ in range(3):
        changed = False
        usable = {k: v for k, v in known.items() if v is not None}
        for d in decls:
            lowered = [t.lower() for t in d.tparams]
            hit = None
            for name, args in d.bases:
                got = _consumed_arg(name, args, usable)
                if got and got[0].lower() in lowered:
                    hit = _OpenConsumer(lowered.index(got[0].lower()), got[1])
                    break
            key = (d.name.lower(), len(d.tparams))
            if hit is None or key in known and known[key] in (hit, None):
                continue
            # Two classes sharing a name and arity that disagree are not trusted.
            known[key] = None if key in known else hit
            changed = True
        if not changed:
            break
    return {k: v for k, v in known.items() if v is not None}


def _brace_end(masked: str, i: int) -> int:
    depth = 0
    for j in range(i, min(len(masked), i + _MAX_BODY)):
        if masked[j] == "{":
            depth += 1
        elif masked[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


def _param_type(param: str, vb: bool) -> str:
    """Last segment of a parameter's declared type, lower-cased ("" when unknown)."""
    m = _VB_PARAM_TYPE.search(param) if vb else _CS_PARAM_TYPE.match(param.strip())
    return m.group(1).rsplit(".", 1)[-1].lower() if m else ""


def _is_extension(masked: str, m: re.Match[str], params: list[str], vb: bool) -> bool:
    if vb:
        return "extension" in masked[max(0, m.start() - 80) : m.start()].lower()
    return bool(params) and params[0].startswith("this ")


def _generic_method_bodies(
    masked: str, vb: bool
) -> Iterator[tuple[str, tuple[str, ...], int, int, list[str]]]:
    """``(name, type parameters, body start, body end, extension parameters)`` per generic method.

    The parameters are empty unless the method is an extension method.
    """
    for m in (_VB_GENERIC_METHOD if vb else _CS_GENERIC_METHOD).finditer(masked):
        if not vb and masked[: m.start()].rstrip().endswith("new"):
            continue
        g = _generic_at(masked, m.end(), vb)
        if not g:
            continue
        p = _skip_ws(masked, g[1], vb)
        end = _balanced(masked, p, "(", ")") if masked[p : p + 1] == "(" else -1
        if end < 0:
            continue
        tparams = _tparam_names(g[0])
        params = _split_args(masked[p + 1 : end - 1])
        if not _is_extension(masked, m, params, vb):
            continue
        if vb:
            stop = _VB_METHOD_END.search(masked, end, end + _MAX_BODY)
            if stop:
                yield m.group(1), tparams, end, stop.start(), params
            continue
        j = end
        while where := _CS_WHERE.match(masked, j):
            j = where.end()
        j = _skip_ws(masked, j, vb)
        if masked[j : j + 1] == "{":
            stop_at = _brace_end(masked, j)
        elif masked[j : j + 2] == "=>":
            semi = masked.find(";", j, j + _MAX_BODY)
            stop_at = semi
        else:
            continue
        if stop_at >= 0:
            yield m.group(1), tparams, j, stop_at, params


def _registrations(
    masked: str, vb: bool, start: int, end: int, known: Mapping[tuple[str, int], _OpenConsumer]
) -> Iterator[tuple[str, bool]]:
    """``(message type, fault)`` of each ``AddConsumer`` of an open generic in a span."""
    for m in (_REGISTER_VB if vb else _REGISTER_CS).finditer(masked, start, end):
        g = _generic_at(masked, m.end(), vb)
        ref = _base_ref(g[0][0], vb) if g else None
        got = _consumed_arg(*ref, known) if ref and ref[0].lower() != "iconsumer" else None
        if got:
            yield got


def _generic_helpers(
    masked: str, vb: bool, known: Mapping[tuple[str, int], _OpenConsumer]
) -> Iterator[tuple[str, int, list[tuple[int, bool]], tuple[int, int]]]:
    for name, tparams, start, end, params in _generic_method_bodies(masked, vb):
        first = _param_type(params[0].removeprefix("this "), vb) if params else ""
        if not _CONFIGURATOR_TYPE.fullmatch(first):
            continue
        lowered = [t.lower() for t in tparams]
        regs = [
            (lowered.index(arg.lower()), fault)
            for arg, fault in _registrations(masked, vb, start, end, known)
            if arg.lower() in lowered
        ]
        if regs:
            yield name, len(tparams), regs, (start, end)


def _param_name(param: str, vb: bool) -> str | None:
    text = param.strip()
    m = _VB_PARAM_NAME.match(text) if vb else _CS_PARAM_NAME.search(text)
    return m.group(1).lower() if m else None


def _owner_at(masked: str, offset: int, vb: bool) -> str:
    owners = list((_VB_OWNER if vb else _CS_OWNER).finditer(masked, 0, offset))
    return owners[-1].group(1).lower() if owners else ""


def _extension_wrappers(masked: str, vb: bool) -> Iterator[tuple[str, _Wrapper]]:
    """Extension methods on a bus type that publish or send their message parameter."""
    for m in (_VB_EXTENSION if vb else _CS_EXTENSION).finditer(masked):
        name = m.group(1)
        if name in _BASE_CALLS:
            continue
        if vb:
            g = _generic_at(masked, _skip_ws(masked, m.end(), vb), vb)
            tparams = _tparam_names(g[0]) if g else ()
            p = _skip_ws(masked, g[1] if g else m.end(), vb)
        else:
            tparams = _tparam_names(_split_args(m.group(2))) if m.group(2) else ()
            p = masked.find("(", m.end(1))
        end = _balanced(masked, p, "(", ")") if masked[p : p + 1] == "(" else -1
        params = _split_args(masked[p + 1 : end - 1]) if end >= 0 else []
        if len(params) < 2 or _param_type(params[0].removeprefix("this "), vb) not in _BUS_TYPES:
            continue
        receiver = _param_name(params[0].removeprefix("this "), vb)
        message = _param_name(params[1], vb)
        lowered = [t.lower() for t in tparams]
        mtype = _param_type(params[1], vb)
        if mtype in lowered:
            tindex: int | None = lowered.index(mtype)
        elif mtype == "object":
            tindex = None
        else:
            continue
        if vb:
            stop = _VB_METHOD_END.search(masked, end, end + _MAX_BODY)
            body = masked[end : stop.start()] if stop else ""
        else:
            j = end
            while where := _CS_WHERE.match(masked, j):
                j = where.end()
            j = _skip_ws(masked, j, vb)
            stop_at = _brace_end(masked, j) if masked[j : j + 1] == "{" else masked.find(";", j)
            body = masked[j:stop_at] if stop_at >= 0 else ""
        for call in (_VB_BUS_CALL if vb else _CS_BUS_CALL).finditer(body):
            if call.group(1).lower() == receiver and call.group(3).lower() == message:
                owner = _owner_at(masked, m.start(), vb)
                yield name, _Wrapper(call.group(2).capitalize(), owner, len(tparams), tindex)
                break


def _prescan(
    sources: Sequence[tuple[str, bool, str]], mask: Callable[[str, bool, str], str]
) -> _RepoScan:
    scan = _RepoScan()
    conflicts: set[str] = set()
    decls: list[_ClassDecl] = []
    scanned: set[str] = set()
    for rel, vb, content in sources:
        if (".Publish" in content or ".Send" in content) and (
            "this " in content or "Extension" in content
        ):
            for name, wrapper in _extension_wrappers(mask(rel, vb, content), vb):
                seen = scan.wrappers.setdefault(name, [wrapper])
                if wrapper not in seen:
                    seen.append(wrapper)
                if (seen[0].base, seen[0].owner) != (wrapper.base, wrapper.owner):
                    conflicts.add(name)
    for name in conflicts:
        del scan.wrappers[name]
    for _ in range(3):
        for rel, vb, content in sources:
            if rel not in scanned and (
                "Consumer" in content or any(n in content for n in scan.open_names)
            ):
                scanned.add(rel)
                decls += _class_decls(mask(rel, vb, content), vb, generic_only=True)
        scan.open_consumers = _open_consumers(decls)
        names = {d.name for d in decls if (d.name.lower(), len(d.tparams)) in scan.open_consumers}
        if names == scan.open_names:
            break
        scan.open_names = names
    if not scan.open_consumers:
        return scan
    clashes: set[tuple[str, int]] = set()
    for rel, vb, content in sources:
        if "Consumer" not in content:
            continue
        masked = mask(rel, vb, content)
        for name, arity, regs, span in _generic_helpers(masked, vb, scan.open_consumers):
            key = (name.lower(), arity)
            if scan.helpers.setdefault(key, regs) != regs:
                clashes.add(key)
            scan.helper_names.add(name)
            scan.helper_spans.setdefault(rel, []).append(span)
    for key in clashes:
        del scan.helpers[key]
    return scan


def _written_type(arg: str) -> str | None:
    return arg if _DOTTED.fullmatch(_GLOBAL_PREFIX.sub("", arg)) else None


def _configurator_receiver(masked: str, start: int, vb: bool) -> bool:
    """Whether the receiver before a helper call can be a registration configurator."""
    r = _RECEIVER.search(masked[max(0, start - 120) : start].rstrip().removesuffix("."))
    recv = r.group(1) if r else ""
    if not masked[:start].rstrip().endswith(".") or not recv:
        return False
    if not re.fullmatch(r"[A-Za-z_]\w*", recv):
        return True
    typ = _declared_type(masked, start, recv, vb)
    return typ is None or bool(_CONFIGURATOR_TYPE.fullmatch(typ.rsplit(".", 1)[-1]))


def _open_generic_findings(masked: str, vb: bool, rel: str, scan: _RepoScan) -> list[_Finding]:
    """Consumers formed by closing an open-generic consumer class or helper."""
    known = scan.open_consumers
    out: list[_Finding] = []

    def emit(arg: str, fault: bool, offset: int, label: str, optional: bool) -> None:
        written = _written_type(arg)
        if written is None:
            return
        short = written.rsplit(".", 1)[-1]
        symbol = f"{label}<Fault<{short}>>" if fault else f"{label}<{short}>"
        out.append(
            _Finding(written, "consumer", offset, "consumer", symbol, fault, optional=optional)
        )

    for d in _class_decls(masked, vb, generic_only=False):
        lowered = {t.lower() for t in d.tparams}
        for name, args in d.bases:
            got = _consumed_arg(name, args, known) if name.lower() != "iconsumer" else None
            if got and got[0].lower() not in lowered:
                emit(got[0], got[1], d.offset, name, optional=False)

    spans = scan.helper_spans.get(rel, [])
    for m in (_REGISTER_VB if vb else _REGISTER_CS).finditer(masked):
        if any(a <= m.start() < b for a, b in spans):
            continue
        g = _generic_at(masked, m.end(), vb)
        ref = _base_ref(g[0][0], vb) if g else None
        got = _consumed_arg(*ref, known) if ref and ref[0].lower() != "iconsumer" else None
        if got:
            emit(got[0], got[1], m.start(), "AddConsumer", optional=True)

    if scan.helpers:
        names = "|".join(sorted({re.escape(n) for n, _ in scan.helpers}))
        call = re.compile(rf"\b({names})\s*(?=<|\(\s*Of\b)", re.IGNORECASE)
        for m in call.finditer(masked):
            g = _generic_at(masked, m.end(), vb)
            if not g or masked[_skip_ws(masked, g[1], vb) :][:1] != "(":
                continue
            if not _configurator_receiver(masked, m.start(), vb):
                continue
            for idx, fault in scan.helpers.get((m.group(1).lower(), len(g[0])), ()):
                emit(g[0][idx], fault, m.start(), m.group(1), optional=True)
    return out


# ---------------------------------------------------------------------------
# Request clients and saga initialisers
# ---------------------------------------------------------------------------

_CS_REQUEST_CLIENT = re.compile(r"\bIRequestClient\s*(?=<)")
_VB_REQUEST_CLIENT = re.compile(r"\bIRequestClient\s*(?=\(\s*Of\b)", re.IGNORECASE)
_CS_INIT = re.compile(
    r"\.\s*(Publish|Send|Respond)(?:Async)?\s*\(\s*"
    r"(?:[\w.]+\s*,\s*|new\s+\w+\s*\([^()]*\)\s*,\s*)?"
    r"\(?\s*\w+\s*\)?\s*=>\s*\w+\s*\.\s*Init\s*"
    r"<\s*((?:global::)?[\w.]+)\s*>"
)
_VB_INIT = re.compile(
    r"\.\s*(Publish|Send|Respond)(?:Async)?\s*\(\s*Function\s*\(\s*\w+\s*\)\s*\w+\s*\.\s*Init\s*"
    r"\(\s*Of\s+((?:Global\.)?[\w.]+)\s*\)",
    re.IGNORECASE,
)


def _request_findings(masked: str, vb: bool) -> list[_Finding]:
    out: list[_Finding] = []
    for m in (_VB_REQUEST_CLIENT if vb else _CS_REQUEST_CLIENT).finditer(masked):
        g = _generic_at(masked, m.end(), vb)
        written = _written_type(g[0][0]) if g and len(g[0]) == 1 else None
        if written:
            short = written.rsplit(".", 1)[-1]
            out.append(
                _Finding(written, "provider", m.start(), "request", f"IRequestClient({short})")
            )
    for m in (_VB_INIT if vb else _CS_INIT).finditer(masked):
        kind = _CALL_KIND[m.group(1).capitalize()]
        short = m.group(2).rsplit(".", 1)[-1]
        out.append(_Finding(m.group(2), "provider", m.start(), kind, f"Init({short})"))
    return out


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

_HINTS = (
    ".Publish",
    ".Send",
    ".Schedule",
    ".Respond",
    "CreateRequestClient",
    "IRequestClient",
    "IConsumer",
    "MassTransitStateMachine",
)


@dataclass(frozen=True)
class _Finding:
    written: str
    role: str
    offset: int
    kind: str
    symbol: str
    fault: bool = False
    dataflow: bool = False
    receiver: str | None = None
    line: int | None = None
    optional: bool = False


_MEDIATOR_TYPES = frozenset({"imediator", "isender", "ipublisher", "mediator"})


def _is_mediator(masked: str, vb: bool, call: _ProducerCall) -> bool:
    """Whether the call's receiver variable is declared as a mediator type."""
    if not re.fullmatch(r"[A-Za-z_]\w*", call.receiver):
        return False
    typ = _declared_type(masked, call.start, call.receiver, vb)
    return typ is not None and typ.rsplit(".", 1)[-1].lower() in _MEDIATOR_TYPES


def _producer_findings(
    masked: str, vb: bool, scan: _RepoScan, call_re: re.Pattern[str]
) -> list[_Finding]:
    out: list[_Finding] = []
    for call in _find_producers(masked, vb, call_re, scan.wrappers):
        if "mediat" in call.receiver.lower() or _is_mediator(masked, vb, call):
            continue
        out.append(
            _Finding(
                call.written,
                "provider",
                call.start,
                _CALL_KIND.get(call.base, "schedule"),
                f"{call.method}({call.written.rsplit('.', 1)[-1]})",
                dataflow=call.dataflow,
                receiver=call.receiver,
            )
        )
    return out


def _consumer_findings(masked: str, vb: bool) -> list[_Finding]:
    out: list[_Finding] = []
    consume_lines: dict[str, int] = {}
    for m in (_VB_CONSUME_METHOD if vb else _CS_CONSUME_METHOD).finditer(masked):
        consume_lines.setdefault(_norm_generic(m.group(1)), line_at(masked, m.start()))

    for m in (_VB_CONSUMER if vb else _CS_CONSUMER).finditer(masked):
        inner = m.group(1)
        fault = _FAULT.match(inner)
        written = fault.group(1) if fault else inner
        if not _DOTTED.fullmatch(_GLOBAL_PREFIX.sub("", written)):
            continue
        short = written.rsplit(".", 1)[-1]
        out.append(
            _Finding(
                written,
                "consumer",
                m.start(),
                "consumer",
                f"IConsumer<Fault<{short}>>" if fault else f"IConsumer<{short}>",
                fault=fault is not None,
                line=consume_lines.get(_norm_generic(inner)),
            )
        )

    if (_VB_STATE_MACHINE if vb else _CS_STATE_MACHINE).search(masked):
        for m in (_VB_SAGA_EVENT if vb else _CS_SAGA_EVENT).finditer(masked):
            short = m.group(1).rsplit(".", 1)[-1]
            out.append(_Finding(m.group(1), "consumer", m.start(), "saga", f"Event<{short}>"))
    return out


_VB_TYPE_DECL = re.compile(
    r"^[ \t]*(?:\w+[ \t]+)*?(?:Class|Structure|Interface|Enum|Module)[ \t]+(\w+)",
    re.IGNORECASE | re.MULTILINE,
)


def _local_type_names(
    sources: Sequence[tuple[str, bool, str]], index: MessageTypeIndex
) -> set[str]:
    """Lower-cased names of the types the repo declares that are not indexed messages."""
    names: set[str] = set()
    for _, vb, content in sources:
        if vb:
            names.update(m.group(1).lower() for m in _VB_TYPE_DECL.finditer(content))
            continue
        for decl in scan_type_declarations(content):
            if decl.fqn not in index.fqns:
                names.add(decl.name.lower())
    return names


class MassTransitExtractor:
    """Extract MassTransit producer and consumer contracts from C# and VB.NET."""

    def __init__(self, contract_project_pattern: str = DEFAULT_CONTRACT_PROJECT_PATTERN) -> None:
        self._contract_project_pattern = contract_project_pattern

    @classmethod
    def source_extensions(cls) -> frozenset[str]:
        return CSHARP | VBNET

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
        files: Sequence[SourceFile] | None = None,
        message_types: MessageTypeIndex | None = None,
        stats: dict[str, int] | None = None,
    ) -> list[Contract]:
        from repowise.core.workspace.contracts import Contract

        if message_types is None:
            message_types = build_message_type_index(
                {repo_alias: repo_path}, exclude, self._contract_project_pattern
            )
        if not message_types.fqns:
            return []
        resolver = _Resolver(message_types)
        counters = stats if stats is not None else {}
        contracts: list[Contract] = []
        seen: set[tuple[str, str, str, bool]] = set()

        sources = [
            (rel_path, suffix in VBNET, content.removeprefix("\ufeff"))
            for rel_path, suffix, content in select_files(
                repo_path, self.source_extensions(), exclude, files
            )
            if not _in_test_project(rel_path)
        ]
        masks: dict[str, str] = {}

        def mask(rel_path: str, vb: bool, content: str) -> str:
            if rel_path not in masks:
                masks[rel_path] = _mask_non_code(content, vb)
            return masks[rel_path]

        local_cache: list[set[str]] = []

        def local_names() -> set[str]:
            if not local_cache:
                local_cache.append(_local_type_names(sources, message_types))
            return local_cache[0]

        scan = _prescan(sources, mask)
        call_re = _call_pattern(scan.wrappers)
        hints = _HINTS + tuple(scan.wrappers) + tuple(scan.helper_names) + tuple(scan.open_names)

        for rel_path, vb, content in sources:
            if not any(h in content for h in hints):
                continue
            masked = mask(rel_path, vb, content)
            ctx = _file_context(masked, vb)
            findings = (
                _producer_findings(masked, vb, scan, call_re)
                + _request_findings(masked, vb)
                + _consumer_findings(masked, vb)
            )
            if scan.open_consumers:
                findings += _open_generic_findings(masked, vb, rel_path, scan)

            for f in findings:
                res = resolver.resolve(f.written, ctx, local_names)
                if isinstance(res, str):
                    if f.optional:
                        continue
                    name = "masstransit_ambiguous" if res == "ambiguous" else (
                        f"masstransit_{f.role.replace('provider', 'producer')}_unresolved"
                    )
                    counters[name] = counters.get(name, 0) + 1
                    continue
                ns, _, short = res.fqn.rpartition(".")
                contract_id = f"topic::{ns}:{short}".lower()
                if (rel_path, contract_id, f.role, f.fault) in seen:
                    continue
                seen.add((rel_path, contract_id, f.role, f.fault))
                meta: dict = {
                    "topic": f"{ns}:{short}",
                    "broker": "masstransit",
                    "message_type": res.fqn,
                    "kind": f.kind,
                    "fault": f.fault,
                    "resolution": "dataflow" if f.dataflow else res.resolution,
                }
                if f.receiver is not None:
                    meta["receiver"] = f.receiver
                confidence = min(res.confidence, _TIER_DATAFLOW) if f.dataflow else res.confidence
                contracts.append(
                    Contract(
                        repo=repo_alias,
                        contract_id=contract_id,
                        contract_type="topic",
                        role=f.role,
                        file_path=rel_path,
                        symbol_name=f.symbol,
                        confidence=confidence,
                        service=None,
                        line=f.line if f.line is not None else line_at(content, f.offset),
                        meta=meta,
                    )
                )
        return contracts

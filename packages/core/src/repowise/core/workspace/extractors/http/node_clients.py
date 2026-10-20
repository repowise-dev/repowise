"""HTTP client libraries for JS / TS: axios, ky, got, ofetch and Angular's HttpClient.

A call goes straight through the library (``axios.post(url)``, ``ky.get(url)``,
``got(url, { method })``) or through an instance created with a base
(``axios.create({ baseURL })``, ``ky.create({ prefixUrl })``,
``got.extend({ prefixUrl })``, ``ofetch.create({ baseURL })``). An instance is
read in the file creating it and in every file importing it from that module,
by name or as its default export: the key is the module's name (what an import
specifier ends with) and the export's, so an instance travels through the
orchestrator's mount pass, and two different instances one key names are read
as neither. A package import (``import http from 'http'``) names no module of
the repo and is never looked up.

A base resolves through the strings layer. One it cannot read
(``process.env.API_URL``) stays a ``${...}`` placeholder, which the consumer
builder records as the call's base token, exactly as for a hand-written
``${API_URL}/users``. ``ofetch(url)`` itself is the ``fetch`` reader's.

Angular's ``HttpClient`` is a type, not a callable: its clients are the
receivers declared with it (:func:`.angular.http_client_receivers`). URLs also fold
what the file imports from an environment module (``environment.apiUrl``) and
class fields set once (``private base = `${environment.apiUrl}/users```), so
``this.http.get(`${this.base}/${id}`)`` reads whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from ..base import line_at
from ..calls import Call, call_sites, file_strings, receiver_at
from ..langs import JS_EXTENSION_RE, JS_TS
from ..strings import NAME_RE, Arg, call_arguments, js_class_fields, select_argument
from .angular import ANGULAR_HTTP, http_client_receivers, imported_environment
from .client_calls import VERBS, is_rooted_url, method_from_argument
from .dialect import build_consumer_contract
from .wrappers import GENERIC_ARGS

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings
    from ..strings import ClassFields


@dataclass(frozen=True)
class _Library:
    name: str
    factories: tuple[str, ...] = ()
    base_key: str = ""  # the option naming an instance's base
    verbs: bool = True  # `client.get(url)`; ofetch has none
    exports: tuple[str, ...] = ("default",)  # what an import of the client names
    specifier: str = ""  # the package, when it is not *name*
    # The import names a type, and the receivers declared with it are the
    # clients (Angular's injected `HttpClient`), not the import itself. Such a
    # client has verbs only; `client(url)` is not a call.
    typed: bool = False
    request: str = ""  # `request({ url, method })` ("config") or `request('GET', url)` ("method")
    body_first: bool = False  # post / put / patch take a body before their options

    @property
    def package(self) -> str:
        return self.specifier or self.name


_LIBRARY_LIST = (
    _Library("axios", ("create",), "baseURL", request="config", body_first=True),
    _Library("ky", ("create", "extend"), "prefixUrl"),
    _Library("got", ("extend",), "prefixUrl"),
    _Library("ofetch", ("create",), "baseURL", verbs=False, exports=("ofetch", "$fetch")),
    _Library(
        "angular",
        exports=("HttpClient",),
        specifier=ANGULAR_HTTP,
        typed=True,
        request="method",
        body_first=True,
    ),
)
_LIBRARIES = {lib.package: lib for lib in _LIBRARY_LIST}
# A library's specifier in quotes, so `ky` is not read out of every word holding it.
_SPECIFIERS = tuple(f"{q}{package}{q}" for package in _LIBRARIES for q in "'\"")
# axios is also a global (a script tag, Laravel's `window.axios`, Nuxt's `$axios`).
_AXIOS_GLOBALS = ("axios.", "axios(")
# Qualifiers a client is called through: its own class, the browser global, Vue.
_QUALIFIER_RE = re.compile(r"(?:this|window|Vue)\s*\.\s*$")

_URL_KEY = Arg(keys=("url",))
_METHOD_KEY = Arg(keys=("method",))

_MOUNT_NAMED = "http-client:"
_MOUNT_DEFAULT = "http-client-default:"
# Present when any file of the repo exports an instance, so a file importing
# none of the libraries is only read for imports then.
_MOUNT_ANY = "http-client-exports"


@dataclass(frozen=True)
class _Client:
    library: _Library
    base: str  # "" for none; an unreadable base is a `${...}` placeholder
    direct: bool = False  # the library itself, not an instance of it


# `import x from 'm'`, `import x, { a, b as c } from 'm'`, `import { a } from 'm'`.
_IMPORT_RE = re.compile(
    r"""import(?<![\w$]import)\s+(?:type\s+)?(?:(?P<default>[A-Za-z_$][\w$]*)\s*,?\s*)?"""
    r"""(?:\{(?P<named>[^{}]*)\}\s*)?from\s*['"](?P<spec>[^'"]+)['"]"""
)
# `const x = require('m')`, the binding read back from the `require`.
_REQUIRE_RE = re.compile(r"""require(?<![\w$]require)\s*\(\s*['"](?P<spec>[^'"]+)['"]\s*\)""")
_REQUIRE_BIND_RE = re.compile(r"(?:const|let|var)\s+(?P<default>[A-Za-z_$][\w$]*)\s*=\s*$")
# What `<client>.create(` is assigned to, read back from the call on its line.
_FACTORY_BIND_RE = re.compile(
    r"(?:(?P<default>export\s+default)|(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"|this\s*\.\s*(?P<field>[A-Za-z_$][\w$]*)"
    r"|(?:(?:private|public|protected|readonly|static)\s+)+(?P<member>[A-Za-z_$][\w$]*))"
    r"(?:\s*:[^=\n]+)?\s*=?\s*(?:await\s+)?$"
)
# `export { api, v2 as client }` and `export default api`.
_EXPORT_LIST_RE = re.compile(r"export(?<![\w$]export)\s*\{(?P<names>[^{}]*)\}")
_EXPORT_DEFAULT_RE = re.compile(
    r"export(?<![\w$]export)\s+default\s+(?P<name>[A-Za-z_$][\w$]*)\s*;?\s*$", re.MULTILINE
)


def _named(entries: str) -> list[tuple[str, str]]:
    """``(name, alias)`` per ``a`` / ``a as b`` entry of an import or export list."""
    out: list[tuple[str, str]] = []
    for entry in entries.split(","):
        parts = entry.split()
        if parts and parts[0] == "type":
            parts = parts[1:]
        if len(parts) == 1:
            out.append((parts[0], parts[0]))
        elif len(parts) == 3 and parts[1] == "as":
            out.append((parts[0], parts[2]))
    return out


def _imports(content: str) -> list[tuple[str, str, str]]:
    """``(local, imported, specifier)`` per binding; ``imported`` is ``default`` for a default."""
    out: list[tuple[str, str, str]] = []
    if "import" in content:
        for m in _IMPORT_RE.finditer(content):
            if m.group("default"):
                out.append((m.group("default"), "default", m.group("spec")))
            out.extend((alias, name, m.group("spec")) for name, alias in _named(m.group("named") or ""))
    if "require" in content:
        for m in _REQUIRE_RE.finditer(content):
            line_start = content.rfind("\n", 0, m.start()) + 1
            bound = _REQUIRE_BIND_RE.search(content, line_start, m.start())
            if bound is not None:
                out.append((bound.group("default"), "default", m.group("spec")))
    return out


def _is_package(spec: str) -> bool:
    """Whether *spec* names an npm package (``http``, ``@scope/sdk``) rather than a module here."""
    if spec.startswith((".", "/", "~", "#", "@/")):
        return False
    parts = spec.split("/")
    return len(parts) == 1 or (spec.startswith("@") and len(parts) == 2)


def _module_name(path: str) -> str:
    """What a specifier naming *path* ends with: its stem, or its directory's for an index."""
    parts = JS_EXTENSION_RE.sub("", path).rstrip("/").split("/")
    if parts[-1] == "index" and len(parts) > 1:
        return parts[-2]
    return parts[-1]


def _key(module: str, export: str) -> str:
    return _MOUNT_DEFAULT + module if export == "default" else f"{_MOUNT_NAMED}{module}:{export}"


def _encode(client: _Client) -> str:
    return f"{client.library.package}\n{client.base}"


def _decode(value: str) -> _Client:
    package, _, base = value.partition("\n")
    return _Client(_LIBRARIES[package], base)


def _alternation(names: tuple[str, ...]) -> str:
    # Longest first, so `$api` is not read as `api` behind a `$`.
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


@lru_cache(maxsize=256)
def _factory_re(names: tuple[str, ...]) -> re.Pattern[str]:
    """``<client>.create(`` / ``.extend(`` on *names*, led by the names so it has a literal start."""
    return re.compile(rf"(?P<parent>{_alternation(names)})\s*\.\s*(?P<factory>create|extend)\s*\(")


@lru_cache(maxsize=256)
def _calls_for(names: tuple[str, ...]) -> tuple[Call, ...]:
    """``x.get(url)``, ``x.request(config)`` and ``x(url)`` on *names*."""
    verbs = "|".join(sorted(VERBS | {"request"}))
    head = re.compile(
        rf"(?P<recv>{_alternation(names)})\s*(?:\.\s*(?P<verb>{verbs})\s*)?{GENERIC_ARGS}\("
    )
    return (Call(head, JS_TS),)


def _base(args: list[str], library: _Library, strings: FileStrings) -> str | None:
    """The base a factory call's options name; ``None`` when they name none."""
    if not library.base_key:
        return None
    raws = select_argument(args, Arg(keys=(library.base_key,)))
    if not raws:
        return None
    text = strings.text(raws[0])
    if text is not None:
        return text
    name = NAME_RE.match(raws[0])
    return f"${{{name.group(0) if name else 'base'}}}"


@dataclass
class _FileClients:
    callable: dict[str, _Client]  # every client this file can call, by local name
    exported: dict[str, _Client]  # what it exports, by exported name (`default` too)


def _file_clients(
    ctx: ScanContext, imports: list[tuple[str, str, str]], strings: FileStrings | None
) -> _FileClients:
    content = ctx.content
    clients: dict[str, _Client] = {}
    for local, imported, spec in imports:
        library = _LIBRARIES.get(spec)
        if library is not None:
            if imported not in library.exports:
                continue
            receivers = http_client_receivers(content, local) if library.typed else [local]
            for name in receivers:
                clients[name] = _Client(library, "", direct=True)
        elif not _is_package(spec):
            value = ctx.mounts.get(_key(_module_name(spec), imported))
            if value is not None:
                clients[local] = _decode(value)
    axios = _LIBRARIES["axios"]
    if "axios" not in clients and any(g in content for g in _AXIOS_GLOBALS):
        clients["axios"] = _Client(axios, "", direct=True)
    if "$axios" in content:
        clients.setdefault("$axios", _Client(axios, "", direct=True))
    exported: dict[str, _Client] = {}
    if not clients or (".create" not in content and ".extend" not in content):
        return _FileClients(clients, exported)

    locals_: dict[str, _Client] = {}
    for m in _factory_re(tuple(clients)).finditer(content):
        parent = clients[m.group("parent")]
        if not receiver_at(content, m.start(), _QUALIFIER_RE) or m.group("factory") not in parent.library.factories:
            continue
        args = call_arguments(content, m.end() - 1)
        if args is None or strings is None:
            continue
        bound = _FACTORY_BIND_RE.search(content, content.rfind("\n", 0, m.start()) + 1, m.start())
        if bound is None:
            continue
        base = _base(args, parent.library, strings)
        client = _Client(parent.library, parent.base if base is None else base)
        name = bound.group("name") or bound.group("field") or bound.group("member")
        if bound.group("default"):
            exported["default"] = client
        elif name:
            locals_[name] = client
            if bound.group("export"):
                exported[name] = client
    if locals_:
        for m in _EXPORT_LIST_RE.finditer(content):
            for name, alias in _named(m.group("names")):
                if name in locals_:
                    exported[alias] = locals_[name]
        named = _EXPORT_DEFAULT_RE.search(content)
        if named is not None and named.group("name") in locals_:
            exported.setdefault("default", locals_[named.group("name")])
    clients.update(locals_)
    return _FileClients(clients, exported)


def client_mounts(ctx: ScanContext) -> dict[str, str]:
    """The client instances *ctx* exports, as mount entries."""
    content = ctx.content
    if ".create" not in content and ".extend" not in content:
        return {}
    if not any(s in content for s in _SPECIFIERS) and "axios.create" not in content:
        return {}  # no library here, so no instance of one (imported ones are not re-read)
    exported = _file_clients(ctx, _imports(content), file_strings(ctx)).exported
    module = _module_name(ctx.rel_path)
    out = {_key(module, name): _encode(client) for name, client in exported.items()}
    if out:
        out[_MOUNT_ANY] = ""
    return out


# axios verbs that take a body before their options.
_BODY_VERBS = frozenset({"post", "put", "patch"})


def _request(
    verb: str | None, args: list[str], client: _Client, strings: FileStrings
) -> tuple[str, str] | None:
    """``(method, url)`` a call names: ``x.post(url)``, ``x(url, { method })``,
    ``x({ url, method })``, ``x.request('GET', url)``.

    A base in the call's own options (``axios.get(url, { baseURL })``) wins over
    the client's, as it does at run time.
    """
    if not args:
        return None
    library = client.library
    if verb is not None and verb != "request":
        url_raws, method = args[:1], verb.upper()
        options = args[2:3] if library.body_first and verb in _BODY_VERBS else args[1:2]
    elif verb == "request" and library.request == "method":
        named = method_from_argument(args[0])
        if named is None:
            return None  # a variable, or an `HttpRequest` object
        url_raws, method, options = args[1:2], named, args[2:3]
    else:
        config = args[0].startswith("{")
        if verb == "request" and not config:
            return None  # `request` takes a config object
        url_raws = select_argument(args[:1], _URL_KEY) if config else args[:1]
        options = args[:1] if config else args[1:2]
        methods = select_argument(options, _METHOD_KEY)
        method = (method_from_argument(methods[0]) if methods else None) or "GET"
    url = strings.text(url_raws[0]) if url_raws else None
    if url is None:
        return None
    base = _base(options, library, strings)
    return method, _join(client.base if base is None else base, url)


class _Fields:
    """The class fields in scope at a call, settled into the file's strings.

    Only the innermost class holding the call is in scope, so a field of one
    class never folds into another's URL. Holes are allowed, and a field built
    on another (``users = `${this.base}/users```) resolves on the second
    round, once the first has settled ``this.base``.
    """

    def __init__(self, strings: FileStrings) -> None:
        self._strings = strings
        self._classes: list[ClassFields] | None = None
        self._current: ClassFields | None = None

    def enter(self, pos: int) -> None:
        if self._classes is None:
            self._classes = js_class_fields(self._strings.content)
        found = next((c for c in self._classes if c.start <= pos < c.end), None)
        if found is self._current:
            return
        settled = self._strings.settled
        for name in self._current.fields if self._current else ():
            settled.pop(name, None)
        self._current = found
        if found is None:
            return
        for _ in range(2):
            for name, rhs in found.fields.items():
                text = self._strings.text(rhs)
                if text is not None:
                    settled[name] = text


def _join(base: str, url: str) -> str:
    if not base or url.startswith(("http://", "https://", "//")):
        return url
    return base.rstrip("/") + "/" + url.lstrip("/") if url else base


def client_calls(ctx: ScanContext) -> tuple[list[Contract], frozenset[str]]:
    """Consumer contracts for the library and instance calls in *ctx*, and the names read."""
    content = ctx.content
    if not (
        any(s in content for s in _SPECIFIERS)
        or "axios" in content
        or (_MOUNT_ANY in ctx.mounts and ("import" in content or "require" in content))
    ):
        return [], frozenset()
    strings = file_strings(ctx)
    if strings is None:
        return [], frozenset()
    imports = _imports(content)
    strings.settled.update(imported_environment(ctx, imports))
    clients = _file_clients(ctx, imports, strings).callable
    # `ofetch(url)` is a fetch call the fetch reader already reads.
    names = tuple(n for n, c in clients.items() if not (c.direct and not c.library.verbs))
    if not names:
        return [], frozenset()
    fields = _Fields(strings)
    out: list[Contract] = []
    for _call, args, m, _strings in call_sites(ctx, _calls_for(names), strings):
        if any("this." in a for a in args[:3]):
            fields.enter(m.start())  # read only for a call whose URL may name a field
        if not receiver_at(content, m.start(), _QUALIFIER_RE):
            continue
        client = clients[m.group("recv")]
        library = client.library
        verb = m.group("verb")
        if (verb in VERBS and not library.verbs) or (verb is None and library.typed):
            continue
        if verb == "request" and not library.request:
            continue
        request = _request(verb, args, client, strings)
        if request is None:
            continue
        method, url = request
        if not is_rooted_url(url):
            continue  # relative to a base this cannot see
        c = build_consumer_contract(
            ctx, method=method, url=url, client=client.library.name, line=line_at(content, m.start())
        )
        if c is not None:
            out.append(c)
    return out, frozenset(names)


__all__ = ["client_calls", "client_mounts"]

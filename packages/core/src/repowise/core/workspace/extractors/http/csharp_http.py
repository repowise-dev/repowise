"""C# HTTP consumer dialect.

Recognises the call shapes common in C# / Unity service clients:

* ``HttpClient`` and wrapper methods: ``GetAsync`` / ``PostAsync`` /
  ``GetRequest<T>`` / ``PostRequest<T>``, the ``System.Net.Http.Json`` forms
  (``GetFromJsonAsync<T>`` / ``PostAsJsonAsync``) and ``GetStringAsync``;
* ``new HttpRequestMessage(HttpMethod.Post, url)``, the request ``SendAsync``
  sends;
* ``UnityWebRequest.Get/Post/Put/Delete`` with a literal or interpolated URL;
* Best.HTTP: ``new HTTPRequest(new Uri("..."), HTTPMethods.Get)``.

An HttpClient URL may be a literal, an interpolated string, or a name: a local
its enclosing block declares (``var uri = $"..."``) or a ``const`` /
``readonly`` string field. A field folds into an interpolation too, so
``$"{remoteServiceBaseUrl}items/{id}"`` keeps the field's ``api/catalog/``
prefix; any other leading hole is the base placeholder and is stripped, and
interior holes collapse to ``{param}``. The query string is cut before the URL
is read, so a hole there the strings layer refuses costs nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import replace
from functools import lru_cache, partial
from typing import TYPE_CHECKING

from repowise.core.analysis.security.masking import mask_comments_and_strings, mask_dialect
from repowise.core.workspace.origins import ContractOrigin

from ..langs import CSHARP
from ..strings import (
    CSHARP_SYNTAX,
    LOCAL_WINDOW,
    NAME_RE,
    call_arguments,
    enclosing_local,
    match_paren,
    resolve_string,
    string_constants,
)
from .client_calls import ClientCallMatch, consumer_contracts, matches_in, method_from_argument
from .csharp_clients import (
    ARG,
    GENERIC,
    JSON_FORMS,
    NAMED_CLIENT_KEY,
    TYPED_CLIENT_KEY,
    VERB_CALL_RE,
    WRAPPER_KEY,
    client_base,
    client_name,
    created_client,
    declared_type,
    declared_types,
    receiver_at,
    registrations,
    type_at,
    with_base,
    wrappers,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# A C# string-literal argument with an optional interpolation (`$`) / verbatim
# (`@`) prefix: capture group 1 = prefix, group 2 = the inner text.
_STR = r"""(\$?@?)"([^"]*)\""""

_REQUEST_MESSAGE_RE = re.compile(r"\bnew\s+HttpRequestMessage\s*\(")

# UnityWebRequest.Get(...) / .Post(...) — only when the first arg is a string.
_UNITY_RE = re.compile(rf"""\bUnityWebRequest\.(Get|Post|Put|Delete|Head)\s*\(\s*{_STR}""")

# Best.HTTP: new HTTPRequest(new Uri("..."), HTTPMethods.Get) — URL first, then
# the method enum.
_BESTHTTP_RE = re.compile(
    rf"""\bnew\s+HTTPRequest\s*\(\s*new\s+Uri\s*\(\s*{_STR}\s*\)\s*,\s*HTTPMethods\.(Get|Post|Put|Delete|Patch|Head)""",
    re.IGNORECASE,
)

# The enum every Best.HTTP match names, searched once before the costlier regex.
_BESTHTTP_GATE_RE = re.compile("httpmethods", re.IGNORECASE)

# HttpClient calls are read off any receiver; its type is not confirmed.
_ORIGIN = ContractOrigin.NAME_HEURISTIC

# Every `name = rhs;` (`this.name` too) is read, so a readonly field its
# constructor reassigns is retired; `=>` is a lambda or an expression body.
_FIELD_SYNTAX = replace(
    CSHARP_SYNTAX,
    assignment=re.compile(
        r"(?<![=!<>.\w])(?:this\.)?(?P<name>[A-Za-z_]\w*)[ \t]*=(?![=>])[ \t]*(?P<rhs>[^;\n]+);"
    ),
)

# A `const` / `readonly` declaration's name, read off the raw text as a cheap
# gate before the file's fields are folded.
_FIELD_DECL_RE = re.compile(r"\b(?:const|readonly)\b[^;=(){}\n]*?\b(\w+)[ \t]*=")
_WORD_RE = re.compile(r"\w+")

# What may follow a literal whose query was cut: nothing, or a trim of the
# query's own separators (`$"...?{qs}".TrimEnd('&', '?')`).
_TRIM_TAIL_RE = re.compile(r"\.\s*TrimEnd\s*\([^()]*\)")
_LITERAL_OPEN_RE = re.compile(r"(\$@|@\$|\$|@)?\"")


def without_query(expr: str) -> str:
    """*expr* with its literal's query string cut, else *expr* unchanged.

    Only a lone C# literal (or one trimmed by ``TrimEnd``) is cut, at the first
    ``?`` outside its interpolation holes. A hole may hold quotes of its own
    (``{string.Join("&ids=", ids)}``), which is why the body is walked here
    rather than by the strings layer, which refuses such a literal.
    """
    m = _LITERAL_OPEN_RE.match(expr)
    if m is None:
        return expr
    prefix = m.group(1) or ""
    holes, verbatim = "$" in prefix, "@" in prefix
    cut, depth, inner = -1, 0, ""
    i = m.end()
    while i < len(expr):
        ch = expr[i]
        if inner:  # a string inside a hole
            if ch == "\\":
                i += 1
            elif ch == inner:
                inner = ""
        elif depth:
            if ch in "\"'":
                inner = ch
            depth += {"{": 1, "}": -1}.get(ch, 0)
        elif ch == "\\" and not verbatim:
            i += 1
        elif ch == '"':
            if verbatim and expr[i + 1 : i + 2] == '"':
                i += 2  # `""` is a quote inside a verbatim literal
                continue
            tail = expr[i + 1 :].strip()
            if cut < 0 or (tail and not _TRIM_TAIL_RE.fullmatch(tail)):
                return expr
            return expr[:cut] + '"'
        elif ch == "?" and cut < 0:
            cut = i
        elif ch == "{" and holes:
            if expr[i + 1 : i + 2] == "{":
                i += 1
            else:
                depth = 1
        i += 1
    return expr


class _FileNames:
    """The file's code with strings blanked and its foldable fields, read on first use."""

    def __init__(self, content: str) -> None:
        self.content = content
        #: Per-name lookups the client readers make, read once per file.
        self.memo: dict[tuple[str, str], list[tuple[int, str]]] = {}
        self._code: str | None = None
        self._fields: dict[str, str] | None = None
        self._types: tuple[list[int], list[tuple[str, tuple[str, ...]]]] | None = None

    @property
    def types(self) -> tuple[list[int], list[tuple[str, tuple[str, ...]]]]:
        """The file's type declarations, for :func:`.csharp_clients.type_at`."""
        if self._types is None:
            self._types = declared_types(self.code)
        return self._types

    @property
    def code(self) -> str:
        if self._code is None:
            self._code = mask_comments_and_strings(
                self.content, strings=True, hash_comments=False, **mask_dialect(".cs")
            )
        return self._code

    def fields(self, urls: list[str]) -> dict[str, str]:
        """``const`` and ``readonly`` string fields the file assigns once.

        A name the file declares a second time (a parameter or a local, in any
        method or class) is left out, since a use might read that one instead;
        so is a plain field or a local assigned once. Read only when one of
        *urls* names such a field.
        """
        declared = {m.group(1) for m in _FIELD_DECL_RE.finditer(self.content)}
        if not any(w in declared for u in urls for w in _WORD_RE.findall(u)):
            return {}
        if self._fields is None:
            code = self.code
            self._fields = {
                name: rhs
                # Read off the source: the mask blanks a literal's quotes too.
                for name, rhs in string_constants(self.content, _FIELD_SYNTAX).items()
                if re.search(rf"\b(?:const|readonly)\b[^;=(){{}}]*\b{re.escape(name)}\s*=", code)
                and len(re.findall(rf"[\w>\]?][ \t]+{re.escape(name)}\s*[,)=;]", code)) == 1
            }
        return self._fields

    def url(self, expr: str, offset: int) -> str:
        """*expr* with a local name replaced by what its block assigned it."""
        # The raw text must assign the name near the use before the file is masked.
        if NAME_RE.fullmatch(expr) and "." not in expr:
            assigned = re.compile(rf"\b{re.escape(expr)}[ \t]*=")
            if assigned.search(self.content, max(0, offset - LOCAL_WINDOW), offset) is None:
                return without_query(expr)
            local = enclosing_local(self.content, self.code, expr, offset)
            if local is not None:
                expr = local
        return sole_hole(without_query(expr))

    def resolve(self, expr: str, offset: int) -> str | None:
        """The text *expr* at *offset* denotes, through locals and fields, else ``None``."""
        url = self.url(expr, offset)
        return resolve_string(url, CSHARP_SYNTAX, self.fields([url]))


_SOLE_HOLE_RE = re.compile(r'(?:\$@|@\$|\$)"\{(?!\{)')


def sole_hole(expr: str) -> str:
    """``$"{x}"`` as ``x``: a literal that interpolates one value and nothing else.

    The value may hold quotes of its own (``$"{Combine(b, $"{p}/x")}"``), which
    the strings layer refuses inside a literal but reads as an expression.
    """
    m = _SOLE_HOLE_RE.match(expr)
    if m is None or not expr.endswith('}"'):
        return expr
    close = match_paren(expr, m.end() - 1, closer="}")
    return expr[m.end() : close] if close == len(expr) - 2 else expr


def _base_for(
    names: _FileNames, mounts: Mapping[str, str], offset: int, receiver: str | None
) -> str | None:
    """The ``BaseAddress`` a call at *offset* on *receiver* resolves against, else ``None``.

    A receiver bound to ``CreateClient("name")`` takes that named client's base;
    a name the file declares ``HttpClient`` takes the base registered for the
    type holding the call. A call with no receiver, or on a member chain, is
    not known to be the client's and takes none.
    """
    if not receiver:
        return None
    token = created_client(names, receiver, offset)
    if token is not None:
        name = client_name(token, names, offset)
        return client_base(mounts, NAMED_CLIENT_KEY + name) if name else None
    if declared_type(names, receiver, offset) != "HttpClient":
        return None
    return _owner_base(names, mounts, offset)


def _owner_base(names: _FileNames, mounts: Mapping[str, str], offset: int) -> str | None:
    owner = type_at(names.types, offset)
    return client_base(mounts, TYPED_CLIENT_KEY + owner[0]) if owner else None


def _has_bases(mounts: Mapping[str, str]) -> bool:
    return any(k.startswith((TYPED_CLIENT_KEY, NAMED_CLIENT_KEY)) for k in mounts)


def httpclient_calls(
    content: str,
    names: _FileNames,
    mounts: Mapping[str, str] | None = None,
    skip: frozenset[int] = frozenset(),
) -> Iterator[ClientCallMatch]:
    """Verb calls; one at an offset in *skip* was already read as a wrapper call."""
    mounts = mounts or {}
    bases = _has_bases(mounts)
    for m in VERB_CALL_RE.finditer(content):
        if m.start() in skip:
            continue
        args = call_arguments(content, m.end() - 1)
        if not args:
            continue
        json_form = m.group(2) in JSON_FORMS
        origin = ContractOrigin.CLIENT_CALL if json_form else _ORIGIN
        base = _base_for(names, mounts, m.start(), receiver_at(content, m.start())) if bases else None
        if base is not None and origin is _ORIGIN:
            origin = ContractOrigin.INSTANCE_BASE  # the registration types the receiver
        yield ClientCallMatch(
            client="httpclient",
            url=names.url(args[0], m.start()),
            offset=m.start(),
            method=m.group(1).upper(),
            origin=origin,
            compose=partial(with_base, base) if base is not None else None,
        )


def request_message_calls(
    content: str, names: _FileNames, mounts: Mapping[str, str] | None = None
) -> Iterator[ClientCallMatch]:
    if "HttpRequestMessage" not in content:
        return
    mounts = mounts or {}
    bases = _has_bases(mounts)
    for m in _REQUEST_MESSAGE_RE.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if not args or len(args) != 2:
            continue
        method = method_from_argument(args[0])
        if method is None:
            continue  # a verb held in a variable is not one we know
        base = _owner_base(names, mounts, m.start()) if bases else None
        yield ClientCallMatch(
            client="httpclient",
            url=names.url(args[1], m.start()),
            offset=m.start(),
            method=method,
            origin=ContractOrigin.CLIENT_CALL,
            compose=partial(with_base, base) if base is not None else None,
        )


def _fill(template: str, url: str) -> str:
    return template.replace(ARG, url)


@lru_cache(maxsize=32)
def _wrapper_call_re(methods: frozenset[str]) -> re.Pattern[str]:
    # Led by the method names, so a file pays one literal-prefixed scan.
    alts = "|".join(sorted(re.escape(m) for m in methods))
    return re.compile(rf"\.\s*(?P<name>{alts})\s*(?:{GENERIC})?\s*\(")


def wrapper_calls(
    content: str, names: _FileNames, mounts: Mapping[str, str]
) -> Iterator[ClientCallMatch]:
    """Calls to a wrapper method the repo declares, through a receiver of its type."""
    methods = frozenset(k.rpartition(".")[2] for k in mounts if k.startswith(WRAPPER_KEY))
    if not methods:
        return
    for m in _wrapper_call_re(methods).finditer(content):
        offset = m.start("name")
        receiver = receiver_at(content, offset)
        rtype = declared_type(names, receiver, offset) if receiver else None
        value = mounts.get(f"{WRAPPER_KEY}{rtype}.{m.group('name')}") if rtype else None
        if value is None:
            continue
        verb, index, least, most, template = value.split(" ", 4)
        args = call_arguments(content, m.end() - 1)
        if not args or not int(least) <= len(args) <= int(most):
            continue  # another overload
        yield ClientCallMatch(
            client=m.group("name"),
            url=names.url(args[int(index)], offset),
            offset=offset,
            method=verb,
            origin=ContractOrigin.DATAFLOW,
            # A body that sends its argument as is adds nothing to compose.
            compose=partial(_fill, template) if template != ARG else None,
        )


_FORWARDER_RE = re.compile(r"\.\s*MapForwarder\s*\(")


def forwarder_calls(content: str, names: _FileNames) -> Iterator[ClientCallMatch]:
    """YARP ``MapForwarder(pattern, destinationPrefix[, path])``: every method, forwarded.

    The destination receives the third argument's path when it is a string,
    else the matched request's own path (the pattern).
    """
    if "MapForwarder" not in content:
        return
    for m in _FORWARDER_RE.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if not args or len(args) < 2:
            continue
        target = args[2] if len(args) > 2 and _LITERAL_OPEN_RE.match(args[2].strip()) else args[0]
        prefix = names.url(args[1], m.start())
        yield ClientCallMatch(
            client="yarp-forwarder",
            url=f"new Uri({prefix}, {names.url(target, m.start())})",
            offset=m.start(),
            method="*",
            origin=ContractOrigin.CLIENT_CALL,
        )


def unitywebrequest_calls(content: str) -> Iterator[ClientCallMatch]:
    if "UnityWebRequest" not in content:
        return
    yield from matches_in(
        content,
        _UNITY_RE,
        client="unitywebrequest",
        url_group=3,
        prefix_group=2,
        method_group=1,
        origin=_ORIGIN,
    )


def besthttp_calls(content: str) -> Iterator[ClientCallMatch]:
    if _BESTHTTP_GATE_RE.search(content) is None:
        return
    yield from matches_in(
        content,
        _BESTHTTP_RE,
        client="besthttp",
        url_group=2,
        prefix_group=1,
        method_group=3,
        origin=_ORIGIN,
    )


# What a client call itself needs; the rest of the gate admits registrations
# and wrapper call sites.
_CALL_TOKENS = ("Async", "Request", "request", "REQUEST", "MapForwarder")


class CSharpHttpDialect:
    name = "csharp-http"
    extensions = CSHARP
    # `GetAsync`, `GetRequest`, `HttpRequestMessage`, `UnityWebRequest`,
    # `HTTPRequest` in any case; client registrations and YARP forwarders; and
    # `Task`, which a call to a wrapper method returning one sits beside.
    requires = (*_CALL_TOKENS, "AddHttpClient", "AddRefitClient", "RestService", "Task")

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """Client bases this file registers and the HttpClient wrappers it declares."""
        names = _FileNames(ctx.content)
        return registrations(names) | wrappers(names)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        names = _FileNames(content)
        wrapped = list(wrapper_calls(content, names, ctx.mounts))
        matches = (
            [
                *httpclient_calls(content, names, ctx.mounts, frozenset(m.offset for m in wrapped)),
                *request_message_calls(content, names, ctx.mounts),
                *forwarder_calls(content, names),
                *unitywebrequest_calls(content),
                *besthttp_calls(content),
            ]
            if ctx.has_any(_CALL_TOKENS)
            else []
        )
        if not matches and not wrapped:
            return []
        # A plain literal never pays for the file's fields.
        fields = names.fields([m.url for m in (*matches, *wrapped) if not m.url.startswith('"')])
        # A slash-free string is a key or a name, not a route, unless a wrapper
        # builds a URL around it; one that passes it through proves nothing.
        built = [m for m in wrapped if m.compose is not None]
        passed = [m for m in wrapped if m.compose is None]
        return [
            *consumer_contracts(ctx, [*matches, *passed], CSHARP_SYNTAX, constants=fields, path_only=True),
            *consumer_contracts(ctx, built, CSHARP_SYNTAX, constants=fields),
        ]

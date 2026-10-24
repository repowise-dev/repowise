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
from collections.abc import Iterator
from dataclasses import replace
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
    string_constants,
)
from .client_calls import ClientCallMatch, consumer_contracts, matches_in, method_from_argument

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# A C# string-literal argument with an optional interpolation (`$`) / verbatim
# (`@`) prefix: capture group 1 = prefix, group 2 = the inner text.
_STR = r"""(\$?@?)"([^"]*)\""""

# A generic type argument, two levels of nesting (`<List<Item>>`).
_GENERIC = r"<(?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*>"

# HttpClient + wrapper calls, ending at the argument list. Method verbs are
# PascalCase (C# convention); the `Async`/`Request` suffix is required so we
# don't match unrelated `Get("key")` lookups. Group 2 holds the
# `System.Net.Http.Json` and `GetStringAsync` forms.
_VERB_CALL_RE = re.compile(
    r"\b(Get|Post|Put|Delete|Patch)(FromJson|AsJson|String|ByteArray|Stream)?(?:Async|Request)"
    rf"\s*(?:{_GENERIC})?\s*\("
)
# Extension methods only HttpClient has; `GetStringAsync` is also a cache's.
_JSON_FORMS = frozenset({"FromJson", "AsJson"})

_REQUEST_MESSAGE_RE = re.compile(r"\bnew\s+HttpRequestMessage\s*\(")

# UnityWebRequest.Get(...) / .Post(...) — only when the first arg is a string.
_UNITY_RE = re.compile(rf"""\bUnityWebRequest\.(Get|Post|Put|Delete|Head)\s*\(\s*{_STR}""")

# Best.HTTP: new HTTPRequest(new Uri("..."), HTTPMethods.Get) — URL first, then
# the method enum.
_BESTHTTP_RE = re.compile(
    rf"""\bnew\s+HTTPRequest\s*\(\s*new\s+Uri\s*\(\s*{_STR}\s*\)\s*,\s*HTTPMethods\.(Get|Post|Put|Delete|Patch|Head)""",
    re.IGNORECASE,
)

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
        self._code: str | None = None
        self._fields: dict[str, str] | None = None

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
        return without_query(expr)


def httpclient_calls(content: str, names: _FileNames) -> Iterator[ClientCallMatch]:
    for m in _VERB_CALL_RE.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if not args:
            continue
        json_form = m.group(2) in _JSON_FORMS
        yield ClientCallMatch(
            client="httpclient",
            url=names.url(args[0], m.start()),
            offset=m.start(),
            method=m.group(1).upper(),
            origin=ContractOrigin.CLIENT_CALL if json_form else _ORIGIN,
        )


def request_message_calls(content: str, names: _FileNames) -> Iterator[ClientCallMatch]:
    if "HttpRequestMessage" not in content:
        return
    for m in _REQUEST_MESSAGE_RE.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if not args or len(args) != 2:
            continue
        method = method_from_argument(args[0])
        if method is None:
            continue  # a verb held in a variable is not one we know
        yield ClientCallMatch(
            client="httpclient",
            url=names.url(args[1], m.start()),
            offset=m.start(),
            method=method,
            origin=ContractOrigin.CLIENT_CALL,
        )


def unitywebrequest_calls(content: str) -> Iterator[ClientCallMatch]:
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
    yield from matches_in(
        content,
        _BESTHTTP_RE,
        client="besthttp",
        url_group=2,
        prefix_group=1,
        method_group=3,
        origin=_ORIGIN,
    )


class CSharpHttpDialect:
    name = "csharp-http"
    extensions = CSHARP
    # `GetAsync`, `GetRequest`, `HttpRequestMessage`, `UnityWebRequest`,
    # `HTTPRequest` in any case.
    requires = ("Async", "Request", "request", "REQUEST")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        names = _FileNames(content)
        matches = [
            *httpclient_calls(content, names),
            *request_message_calls(content, names),
            *unitywebrequest_calls(content),
            *besthttp_calls(content),
        ]
        if not matches:
            return []
        # A plain literal never pays for the file's fields.
        fields = names.fields([m.url for m in matches if not m.url.startswith('"')])
        # A slash-free string is a key or a name, not a route.
        return consumer_contracts(ctx, matches, CSHARP_SYNTAX, constants=fields, path_only=True)

"""PHP HTTP consumer dialect: Guzzle and the Laravel ``Http`` facade.

Recognises the two shapes PHP application code actually uses:

* Guzzle's generic form, ``$client->request('GET', $url, ...)``, where the verb
  is the first argument and the URL the second, and its verb-named form,
  ``$client->get($url)``. The generic form is read only in a file that names
  Guzzle, because ``->request(...)`` and ``->get(...)`` are common method names
  on anything.
* The Laravel facade, ``Http::get($url)`` and its chained builders
  (``Http::withToken($t)->post($url)``), read from ``Http::`` forward along the
  chain to the call that names a verb.

The URL argument is handed to the shared resolver, so a literal, an
interpolated string and a concatenation onto a folded constant all resolve,
while a property (``$this->uri``) or a ``config(...)`` lookup is refused: PHP
does not settle either one inside the file.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..langs import PHP
from ..strings import PHP_SYNTAX, call_arguments, match_paren, resolve_string, string_constants
from .client_calls import VERBS, ClientCallMatch, consumer_contracts, method_from_argument

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# A receiver the call hangs off: a variable or one property of one.
_RECEIVER = r"\$[A-Za-z_]\w*(?:->[A-Za-z_]\w*)?"

_GUZZLE_REQUEST_RE = re.compile(rf"(?P<recv>{_RECEIVER})\s*->\s*request\s*\(")
_GUZZLE_VERB_RE = re.compile(
    rf"(?P<recv>{_RECEIVER})\s*->\s*(?P<verb>get|post|put|patch|delete|head)\s*\("
)

# Guzzle is only claimed in a file that names a client: the client class, a
# construction, or the interface a constructor is typed against. Naming the
# package alone is not enough: a file importing only a PSR-7 helper from it
# still calls ``->get(...)`` on things that are not HTTP clients.
_GUZZLE_GATE_RE = re.compile(r"GuzzleHttp\\Client|new\s+(?:Guzzle)?Client\s*\(|\bClientInterface\b")
_GUZZLE_NEW_RE = re.compile(
    r"(?P<var>\$[A-Za-z_]\w*(?:->[A-Za-z_]\w*)?)\s*=\s*new\s+(?:\\?GuzzleHttp\\)?Client\s*\("
)
_BASE_URI_RE = re.compile(r"['\"]base_uri['\"]\s*=>\s*(?P<val>[^,\]\n]+)")

_CURL_INIT_RE = re.compile(r"\bcurl_init\s*\(")
_CURL_SETOPT_RE = re.compile(r"\bcurl_setopt\s*\(")
_CURL_SETOPT_ARRAY_RE = re.compile(r"\bcurl_setopt_array\s*\(")
_CURLOPT_URL_RE = re.compile(r"\bCURLOPT_URL\s*=>\s*(?P<val>[^,\]\n]+)")

_HTTP_FACADE_RE = re.compile(r"\bHttp::")
_CHAIN_LINK_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")
_CHAIN_ARROW_RE = re.compile(r"\s*->\s*")

# How far along a builder chain the verb call is looked for.
_MAX_CHAIN_LINKS = 8


def _names_a_client(content: str) -> bool:
    return "Client" in content and _GUZZLE_GATE_RE.search(content) is not None


def guzzle_bases(content: str, constants: dict[str, str]) -> dict[str, str]:
    """Client variables constructed with a same-file ``base_uri``."""
    out: dict[str, str] = {}
    if not _names_a_client(content) or "base_uri" not in content:
        return out
    for m in _GUZZLE_NEW_RE.finditer(content):
        var = m.group("var")
        open_paren = m.end() - 1
        close_paren = match_paren(content, open_paren)
        if close_paren < 0:
            continue
        args_text = content[open_paren + 1 : close_paren]
        bm = _BASE_URI_RE.search(args_text)
        if bm:
            resolved = resolve_string(bm.group("val").strip(), PHP_SYNTAX, constants)
            if resolved:
                out[var] = resolved.rstrip("/")
    return out


def _is_relative_url(url: str) -> bool:
    return not url.startswith(("http://", "https://", "//"))


def _apply_base_uri(
    recv: str,
    url_arg: str,
    bases: dict[str, str] | None,
    constants: dict[str, str] | None,
    default_origin: ContractOrigin,
) -> tuple[str, ContractOrigin]:
    if not bases or recv not in bases:
        return url_arg, default_origin
    resolved_rel = resolve_string(url_arg, PHP_SYNTAX, constants)
    if resolved_rel and _is_relative_url(resolved_rel):
        base = bases[recv]
        return f"'{base}/{resolved_rel.lstrip('/')}'", ContractOrigin.INSTANCE_BASE
    return url_arg, default_origin


def guzzle_request_calls(
    content: str,
    bases: dict[str, str] | None = None,
    constants: dict[str, str] | None = None,
) -> Iterator[ClientCallMatch]:
    """``$client->request('GET', $url, ...)``: verb first, URL second."""
    if not _names_a_client(content):
        return
    for m in _GUZZLE_REQUEST_RE.finditer(content):
        open_idx = m.end() - 1
        args = call_arguments(content, open_idx)
        if not args or len(args) < 2:
            continue
        method = method_from_argument(args[0])
        if method is None:
            continue  # a runtime verb: this layer has no way to know it
        url_arg, origin = _apply_base_uri(
            m.group("recv"), args[1], bases, constants, ContractOrigin.CLIENT_CALL
        )
        yield ClientCallMatch(
            client="guzzle",
            url=url_arg,
            offset=m.start(),
            method=method,
            origin=origin,
        )


def guzzle_verb_calls(
    content: str,
    bases: dict[str, str] | None = None,
    constants: dict[str, str] | None = None,
) -> Iterator[ClientCallMatch]:
    """``$client->get($url)``: the verb is the callee's name."""
    if not _names_a_client(content):
        return
    for m in _GUZZLE_VERB_RE.finditer(content):
        open_idx = m.end() - 1
        args = call_arguments(content, open_idx)
        if not args:
            continue
        url_arg, origin = _apply_base_uri(
            m.group("recv"), args[0], bases, constants, ContractOrigin.NAME_HEURISTIC
        )
        yield ClientCallMatch(
            client="guzzle",
            url=url_arg,
            offset=m.start(),
            method=m.group("verb").upper(),
            origin=origin,
        )


def _curl_init_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _CURL_INIT_RE.finditer(content):
        open_idx = m.end() - 1
        args = call_arguments(content, open_idx)
        if args and args[0].strip():
            yield ClientCallMatch(
                client="curl",
                url=args[0].strip(),
                offset=m.start(),
                method="GET",
                origin=ContractOrigin.CLIENT_CALL,
            )


def _curl_setopt_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _CURL_SETOPT_RE.finditer(content):
        open_idx = m.end() - 1
        args = call_arguments(content, open_idx)
        if args and len(args) >= 3 and args[1].strip() == "CURLOPT_URL":
            yield ClientCallMatch(
                client="curl",
                url=args[2].strip(),
                offset=m.start(),
                method="GET",
                origin=ContractOrigin.CLIENT_CALL,
            )


def _curl_setopt_array_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _CURL_SETOPT_ARRAY_RE.finditer(content):
        open_idx = m.end() - 1
        close_idx = match_paren(content, open_idx)
        if close_idx < 0:
            continue
        args_text = content[open_idx + 1 : close_idx]
        um = _CURLOPT_URL_RE.search(args_text)
        if um:
            yield ClientCallMatch(
                client="curl",
                url=um.group("val").strip(),
                offset=m.start(),
                method="GET",
                origin=ContractOrigin.CLIENT_CALL,
            )


def curl_calls(content: str) -> Iterator[ClientCallMatch]:
    """``curl_init($url)`` and ``curl_setopt($ch, CURLOPT_URL, $url)``."""
    if "curl_init" not in content and "CURLOPT_URL" not in content:
        return
    yield from _curl_init_calls(content)
    yield from _curl_setopt_calls(content)
    yield from _curl_setopt_array_calls(content)


def laravel_http_calls(content: str) -> Iterator[ClientCallMatch]:
    """``Http::get($url)`` and the chained builders that end in a verb call."""
    if "Http::" not in content:
        return
    for m in _HTTP_FACADE_RE.finditer(content):
        pos = m.end()
        for _ in range(_MAX_CHAIN_LINKS):
            link = _CHAIN_LINK_RE.match(content, pos)
            if link is None:
                break
            open_idx = link.end() - 1
            close = match_paren(content, open_idx)
            if close < 0:
                break
            if link.group(1).lower() in VERBS:
                args = call_arguments(content, open_idx, close)
                if args:
                    yield ClientCallMatch(
                        client="laravel-http",
                        url=args[0],
                        offset=m.start(),
                        method=link.group(1).upper(),
                    )
                break
            arrow = _CHAIN_ARROW_RE.match(content, close + 1)
            if arrow is None:
                break  # the chain ended before naming a verb
            pos = arrow.end()


class PhpClientsDialect:
    name = "php-clients"
    extensions = PHP
    requires = ("Client", "Http::", "curl_init", "CURLOPT_URL")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        constants = string_constants(ctx.content, PHP_SYNTAX)
        bases = guzzle_bases(ctx.content, constants)
        request_rows = list(guzzle_request_calls(ctx.content, bases=bases, constants=constants))
        verb_rows = list(guzzle_verb_calls(ctx.content, bases=bases, constants=constants))
        facade_rows = list(laravel_http_calls(ctx.content))
        curl_rows = list(curl_calls(ctx.content))
        total_rows = len(request_rows) + len(verb_rows) + len(facade_rows) + len(curl_rows)
        if total_rows == 0:
            return []
        # A Guzzle client composes a relative path onto its `base_uri`, so only
        # a rooted or absolute URL names the route reached.
        out = consumer_contracts(
            ctx,
            request_rows,
            PHP_SYNTAX,
            constants=constants,
            rooted_only=True,
        )
        # `->get('key')` on anything is a lookup, and a bare host carries no
        # route, so both verb-named shapes need a path.
        out += consumer_contracts(
            ctx,
            verb_rows,
            PHP_SYNTAX,
            constants=constants,
            path_only=True,
            rooted_only=True,
        )
        out += consumer_contracts(
            ctx,
            facade_rows,
            PHP_SYNTAX,
            constants=constants,
            path_only=True,
            rooted_only=True,
        )
        out += consumer_contracts(
            ctx,
            curl_rows,
            PHP_SYNTAX,
            constants=constants,
            rooted_only=True,
        )
        return out

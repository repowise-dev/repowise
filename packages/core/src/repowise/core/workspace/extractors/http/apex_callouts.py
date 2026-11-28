"""Salesforce Apex HTTP callout consumer dialect.

An Apex callout is a ``System.HttpRequest`` configured statement by statement::

    HttpRequest req = new HttpRequest();
    req.setEndpoint('callout:Weather_Endpoint/weather');
    req.setMethod('GET');
    new Http().send(req);

A row needs, inside one method, an ``HttpRequest`` declared there (or taken as
a parameter), its ``setEndpoint`` and exactly one resolved verb; anything else is
refused rather than guessed. The endpoint must be an absolute URL or
``callout:Name/path``, whose credential name becomes the call's base token
(the ``${API_BASE}/users`` shape) for ``service_bases`` to map. A name folds
only as a ``final`` field or a local the method assigns once. An endpoint
parameter can instead read literal arguments at unambiguous same-class calls
to its wrapper, one level only; a verb parameter must resolve at that call too.

When ``namedCredentials/<Name>.namedCredential-meta.xml`` is present and declares
a literal ``<endpoint>``, the callout's base URL is resolved directly from it.
Otherwise, the credential name is retained as a ``${Name}`` base token for
``service_bases`` to map.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.fs_walk import iter_glob
from repowise.core.ingestion.framework_routes import apex_code
from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..langs import APEX
from ..strings import (
    APEX_SYNTAX,
    NAME_RE,
    call_arguments,
    resolve_string,
    split_top_level,
    string_constants,
)
from .client_calls import method_from_argument
from .dialect import build_consumer_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Apex identifiers and method names are case-insensitive.
_SET_ENDPOINT_RE = re.compile(
    r"(?<!\.)\b(?P<recv>[A-Za-z_]\w*)\s*\.\s*setEndpoint\s*\(", re.IGNORECASE
)
_TYPE_HEADER_RE = re.compile(r"\b(?:class|interface|enum)\b", re.IGNORECASE)
# Only a test class holds `@isTest` or `testMethod`; its requests are fixtures.
_IS_TEST_RE = re.compile(r"@isTest\b|\btestMethod\b", re.IGNORECASE)

# `callout:Name/path`, the name an identifier and the rest a path or query.
_CALLOUT_RE = re.compile(
    r"callout:(?P<name>[A-Za-z]\w*)(?P<rest>[/?].*)?", re.IGNORECASE | re.DOTALL
)
_ABSOLUTE_RE = re.compile(r"https?://[^/$?]+(?:[/?]|$)", re.IGNORECASE)

_METHOD_HEADER_RE = re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*\([^()]*\)\s*$")
_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'", re.DOTALL)


def _blocks(code: str) -> list[tuple[int, int, bool]]:
    """``(open, close, is_type_body)`` per brace block of comment- and string-blanked *code*."""
    out: list[tuple[int, int, bool]] = []
    stack: list[tuple[int, bool]] = []
    boundary = 0  # just past the last `;`, `{` or `}`: where the current header starts
    for i, c in enumerate(code):
        if c == "{":
            stack.append((i, _TYPE_HEADER_RE.search(code, boundary, i) is not None))
            boundary = i + 1
        elif c == "}":
            if stack:
                start, is_type = stack.pop()
                out.append((start, i, is_type))
            boundary = i + 1
        elif c == ";":
            boundary = i + 1
    return out


def _method_scope(
    blocks: list[tuple[int, int, bool]], code: str, offset: int
) -> tuple[int, int] | None:
    """``(header start, body end)`` of the method around *offset*.

    The method body is the outermost enclosing block that is not a type body.
    A file with no enclosing block (an anonymous ``.apex`` script) is one scope;
    a call sitting directly in a type body is not a statement and has none.
    """
    enclosing = sorted((b for b in blocks if b[0] < offset < b[1]), key=lambda b: b[0])
    if not enclosing:
        return 0, len(code)
    for start, end, is_type in enclosing:
        if not is_type:
            # The header holds the parameter list, where a request can be declared.
            header = max(
                code.rfind(";", 0, start), code.rfind("{", 0, start), code.rfind("}", 0, start)
            )
            return header + 1, end
    return None


def _verb(
    content: str,
    code: str,
    recv: str,
    start: int,
    end: int,
    bindings: dict[str, str | None] | None = None,
) -> str | None:
    """The one literal verb *recv*'s ``setMethod`` calls name in ``[start, end)``, else None."""
    pattern = re.compile(rf"\b{re.escape(recv)}\s*\.\s*setMethod\s*\(", re.IGNORECASE)
    verbs: set[str | None] = set()
    for m in pattern.finditer(code, start, end):
        args = call_arguments(content, m.end() - 1)
        expr = args[0] if args and len(args) == 1 else None
        if expr is not None and bindings is not None:
            expr = bindings.get(expr.lower(), expr)
        verbs.add(method_from_argument(expr) if expr is not None else None)
    if len(verbs) != 1:
        return None
    return verbs.pop()


def _cut_query(expr: str) -> str:
    """*expr* up to the literal piece that opens the query string.

    What follows fills query values (``'...json?location=' + req.lat``), which
    never change the path the call reaches but often are expressions the
    strings layer refuses.
    """
    pieces = split_top_level(expr, "+")
    for i, piece in enumerate(pieces):
        if piece.strip().startswith("'") and "?" in piece:
            return "+".join(pieces[: i + 1])
    return expr


def _local_value(content: str, code: str, name: str, start: int, end: int) -> str | None:
    """The right-hand side of *name*'s one assignment in ``[start, end)``, else None."""
    pattern = rf"(?<![.\w]){re.escape(name)}\s*(\+?)=(?!=)"
    assigns = list(re.finditer(pattern, code[start:end], re.IGNORECASE))
    if len(assigns) != 1 or assigns[0].group(1):
        return None
    rhs = start + assigns[0].end()
    semi = code.find(";", rhs, end)
    return content[rhs:semi] if semi != -1 else None


def _declared_in(code: str, name: str, start: int, end: int) -> bool:
    """Whether *name* is declared as a local or parameter in ``[start, end)``."""
    pattern = rf"[\w>]\s+{re.escape(name)}\s*[,)=;:]"
    return re.search(pattern, code[start:end], re.IGNORECASE) is not None


def _final_fields(content: str, code: str) -> dict[str, str]:
    """The file's single-assignment string names that are declared ``final``."""
    return {
        name: rhs
        for name, rhs in string_constants(content, APEX_SYNTAX).items()
        if re.search(rf"\bfinal\b[^;={{}}]*\b{re.escape(name)}\s*=", code, re.IGNORECASE)
    }


_ENDPOINT_TAG_RE = re.compile(r"<endpoint\b[^>]*>(.*?)</endpoint>", re.DOTALL | re.IGNORECASE)


def _find_named_credential_file(repo_root: Path, xml_name: str) -> Path | None:
    candidates = (
        repo_root / "namedCredentials" / xml_name,
        repo_root / "force-app" / "main" / "default" / "namedCredentials" / xml_name,
    )
    for c in candidates:
        if c.is_file():
            return c
    try:
        matches = list(iter_glob(repo_root, f"**/namedCredentials/{xml_name}"))
        return matches[0] if matches and matches[0].is_file() else None
    except OSError:
        return None


def _parse_endpoint_from_xml(file_path: Path) -> str | None:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _ENDPOINT_TAG_RE.search(text)
    if not m:
        return None
    endpoint = m.group(1).strip()
    if "${" in endpoint or not _ABSOLUTE_RE.match(endpoint):
        return None
    return endpoint.rstrip("/")


def _read_named_credential(repo_root: Path | None, name: str) -> str | None:
    if repo_root is None:
        return None
    target = _find_named_credential_file(repo_root, f"{name}.namedCredential-meta.xml")
    return _parse_endpoint_from_xml(target) if target is not None else None


def _resolve_cred_base(
    name: str,
    repo_root: Path | None,
    cred_cache: dict[str, str | None] | None,
) -> str | None:
    if repo_root is None:
        return None
    if cred_cache is not None and name in cred_cache:
        return cred_cache[name]
    base = _read_named_credential(repo_root, name)
    if cred_cache is not None:
        cred_cache[name] = base
    return base


def _endpoint(
    url: str,
    repo_root: Path | None = None,
    cred_cache: dict[str, str | None] | None = None,
) -> str | None:
    """*url* as the consumer layer reads it, or None when its target is not literal."""
    callout = _CALLOUT_RE.fullmatch(url)
    if callout is not None:
        name = callout.group("name")
        rest = callout.group("rest") or ""
        base = _resolve_cred_base(name, repo_root, cred_cache)
        if base:
            if rest and not rest.startswith(("/", "?")):
                rest = f"/{rest}"
            return f"{base}{rest}"
        return "${" + name + "}" + rest
    if _ABSOLUTE_RE.match(url):
        return url
    return None


def _holds_request(code: str, recv: str, start: int, end: int) -> bool:
    """Whether ``[start, end)`` declares *recv* as an ``HttpRequest`` (local or parameter)."""
    declared = re.compile(rf"\bHttpRequest\s+{re.escape(recv)}\b", re.IGNORECASE)
    return declared.search(code, start, end) is not None


def _url(
    content: str, code: str, expr: str, scope: tuple[int, int], constants: dict[str, str]
) -> str | None:
    """The text endpoint argument *expr* denotes, or None when it is not settled here."""
    start, end = scope
    if NAME_RE.fullmatch(expr) and _declared_in(code, expr, start, end):
        # A local or parameter shadows any field of the same name.
        local = _local_value(content, code, expr, start, end)
        if local is None:
            return None
        expr = local
    return resolve_string(_cut_query(expr), APEX_SYNTAX, constants)


def _type_scope(blocks: list[tuple[int, int, bool]], offset: int) -> tuple[int, int, bool] | None:
    """The innermost class/interface/enum containing *offset*."""
    return max(
        (b for b in blocks if b[2] and b[0] < offset < b[1]),
        default=None,
    )


def _wrapper_calls(
    content: str,
    code: str,
    blocks: list[tuple[int, int, bool]],
    scope: tuple[int, int],
    recv: str,
    expr: str,
) -> list[tuple[str, str, int]]:
    """``(verb, URL, caller offset)`` for literal calls to an endpoint wrapper."""
    start, end = scope
    body = code.find("{", start, end)
    header = code[start:body] if body != -1 else ""
    method = _METHOD_HEADER_RE.search(header)
    owner = _type_scope(blocks, start)
    if method is None or owner is None:
        return []
    # Angle brackets nest type arguments in declarations, like brackets nest
    # expressions at a call site. Keep their commas out of parameter splitting.
    params = call_arguments(
        header.replace("<", "[").replace(">", "]"), header.index("(", method.start())
    )
    if not params or any(not p for p in params):
        return []
    names = [p.rsplit(None, 1)[-1].lower() for p in params]
    if expr.lower() not in names or len(set(names)) != len(names):
        return []
    endpoint_pos = names.index(expr.lower())
    name = method.group("name")
    pattern = re.compile(rf"\b{re.escape(name)}\s*\(", re.IGNORECASE)
    sites = [m for m in pattern.finditer(code) if _type_scope(blocks, m.start()) == owner]
    # Headers live directly in the type body, as do field initializers. Only
    # a declaration prefix (modifiers and a return type) counts as an overload.
    declarations: set[int] = set()
    for site in sites:
        if _method_scope(blocks, code, site.start()) is not None:
            continue
        boundary = max(
            code.rfind("{", 0, site.start()),
            code.rfind("}", 0, site.start()),
            code.rfind(";", 0, site.start()),
        )
        prefix = re.sub(r"@\w+(?:\s*\([^()]*\))?", "", code[boundary + 1 : site.start()]).strip()
        if prefix and re.fullmatch(r"[\w.<>,\[\]\s]+", prefix):
            declarations.add(site.start())
    if declarations != {start + method.start("name")}:
        return []
    mutated = {
        n
        for n in names
        if re.search(
            rf"(?<![.\w]){re.escape(n)}\s*[-+*/%&|^]?=(?!=)", code[body:end], re.IGNORECASE
        )
    }
    type_header = code[
        max(
            code.rfind("{", 0, owner[0]), code.rfind("}", 0, owner[0]), code.rfind(";", 0, owner[0])
        )
        + 1 : owner[0]
    ]
    type_name = re.search(r"\bclass\s+([A-Za-z_]\w*)", type_header, re.IGNORECASE)
    out: list[tuple[str, str, int]] = []
    for site in sites:
        if site.start() in declarations:
            continue
        prefix = code[: site.start()].rstrip()
        before = prefix
        if prefix.endswith("."):
            qualifier = re.search(r"(?<![.\w])([A-Za-z_]\w*)\s*\.\s*$", prefix)
            if qualifier is None or prefix[: qualifier.start()].rstrip().endswith("."):
                continue
            before = prefix[: qualifier.start()]
            receiver = qualifier.group(1)
            if receiver.lower() != "this" and (
                type_name is None
                or receiver.lower() != type_name.group(1).lower()
                or _declared_in(code, receiver, owner[0], owner[1])
            ):
                continue
        if re.search(r"\bnew\s*$", before, re.IGNORECASE):
            continue  # constructing a same-named type does not invoke the wrapper
        args = call_arguments(content, site.end() - 1)
        if args is None or len(args) != len(names):
            continue
        bindings = {
            n: arg if n not in mutated and _LITERAL_RE.fullmatch(arg) else None
            for n, arg in zip(names, args, strict=True)
        }
        literal = bindings[names[endpoint_pos]]
        url = resolve_string(literal, APEX_SYNTAX) if literal is not None else None
        verb = _verb(content, code, recv, start, end, bindings)
        if url is not None and verb is not None:
            out.append((verb, url, site.start()))
    return out


def _resolve_repo_root(ctx: ScanContext) -> Path | None:
    if ctx.repo_root is not None:
        return ctx.repo_root
    if ctx.index and ctx.index.repo_path:
        return Path(ctx.index.repo_path)
    return None


def _build_site_contracts(
    ctx: ScanContext,
    content: str,
    sites: list[tuple[str, str, int]],
    repo_root: Path | None,
    cred_cache: dict[str, str | None],
) -> list[Contract]:
    out: list[Contract] = []
    for method, url, offset in sites:
        endpoint = _endpoint(url, repo_root, cred_cache)
        if endpoint is None:
            continue
        c = build_consumer_contract(
            ctx,
            method=method,
            url=endpoint,
            client="apex-callout",
            line=line_at(content, offset),
            origin=ContractOrigin.CLIENT_CALL,
        )
        if c is not None:
            c.symbol_name = f"apex-callout:{method} {url}"  # as written, not rewritten
            out.append(c)
    return out


class ApexCalloutsDialect:
    name = "apex-callouts"
    extensions = APEX
    # Apex is case-insensitive; `extract` reads the exact spelling.
    requires = ("setEndpoint", "setendpoint", "SetEndpoint", "SETENDPOINT")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if "setendpoint" not in content.lower():
            return []
        code = apex_code(content)
        if _IS_TEST_RE.search(code):
            return []
        blocks = _blocks(code)
        constants: dict[str, str] | None = None
        repo_root = _resolve_repo_root(ctx)
        cred_cache: dict[str, str | None] = {}
        out: list[Contract] = []
        for m in _SET_ENDPOINT_RE.finditer(code):
            recv = m.group("recv")
            scope = _method_scope(blocks, code, m.start())
            if scope is None:
                continue
            start, end = scope
            if not _holds_request(code, recv, start, end):
                continue  # not a request this method holds
            method = _verb(content, code, recv, start, end)
            args = call_arguments(content, m.end() - 1)
            if not args or len(args) != 1:
                continue
            if constants is None:
                constants = _final_fields(content, code)
            url = _url(content, code, args[0], scope, constants)
            sites = (
                [(method, url, m.start())]
                if method is not None and url is not None
                else _wrapper_calls(content, code, blocks, scope, recv, args[0])
            )
            out.extend(_build_site_contracts(ctx, content, sites, repo_root, cred_cache))
        return out

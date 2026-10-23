"""Java and Kotlin HTTP consumer dialects.

Five call shapes, each with its own source for the verb:

* Feign declarative clients: ``@RequestLine("POST /v1/{entityType}/images")``.
  Verb and path both sit inside one annotation string and the placeholders are
  already ``{param}`` shaped, so this is the cleanest Java shape there is.
* ``java.net.http``: ``HttpRequest.newBuilder(URI.create(url))`` or
  ``HttpRequest.newBuilder().uri(URI.create(url))``, with the verb read from the
  builder terminal (``.GET()``, ``.POST(...)``, ``.method("PATCH", ...)``) found
  by walking the chain up to its ``;`` or its ``.build()``.
* Spring ``RestTemplate``: ``getForObject`` / ``postForEntity`` and friends name
  the verb in the callee, ``exchange(uri, HttpMethod.GET, ...)`` names it in the
  second argument, or in the ``RequestEntity.get(uri)`` it is handed. The
  template may be reached through a getter (``proxy.getRestTemplate()``).
* Spring ``WebClient`` and ``RestClient``: ``client.get().uri(url, vars)``, the
  verb step (or ``.method(HttpMethod.X)``) directly before the ``uri`` step.
* Apache HttpClient: ``new HttpPost(url)``, the class naming the verb.

A helper constructed with its base URL in another file
(``new CartHelper(CART_URL)`` beside ``this.baseUrl = baseUrl``) has that base
folded in: constructor calls passing a URL are collected repo-wide as mounts,
and a call whose URL starts with such a field is a dataflow row.

Spring's declarative clients, Java or Kotlin, are read by their own dialect:
an ``@FeignClient`` interface written with the controller annotations, and a
Spring 6 ``@HttpExchange`` interface. Both are parsed by the Spring provider
reader in ``ingestion.framework_routes``, so a client and the controller it
calls read one grammar.

A chain with no terminal verb is refused rather than defaulted to GET, and a URL
that is a bare variable is refused unless the file assigns it a string literal
exactly once. OkHttp is left out: the corpus builds its ``Request.Builder`` from
a URL validated upstream, so no site carries a path this layer could read.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    SPRING_EXCHANGE,
    RouteMatch,
    jaxrs_routes,
    micronaut_annotations,
    microprofile_rest_clients,
    spring_class_paths,
    spring_feign_clients,
    spring_route_prefixes,
    spring_routes,
)
from repowise.core.workspace.origins import ContractOrigin

from ..langs import JAVA, KOTLIN
from ..strings import (
    JAVA_SYNTAX,
    call_arguments,
    match_paren,
    resolve_string,
    split_top_level,
    string_constants,
    syntax_for_suffix,
)
from .client_calls import VERBS, ClientCallMatch, consumer_contracts, method_from_argument
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# @RequestLine("POST /v1/{entityType}/images"): verb then path, one literal.
_REQUEST_LINE_RE = re.compile(r"@RequestLine\s*\(\s*\"([A-Za-z]+)[ \t]+([^\"\n]*)\"")

# The head of a java.net.http builder chain.
_NEW_BUILDER_RE = re.compile(r"\bHttpRequest\s*\.\s*newBuilder\s*\(")

# One `.name(` step of a fluent chain.
_CHAIN_STEP_RE = re.compile(r"\.\s*([A-Za-z_]\w*)\s*\(")

# How far past the chain head a terminal may sit. A java.net.http request is
# built in a handful of lines; beyond that the scan is reading unrelated code.
_CHAIN_LIMIT = 2000

# RestTemplate calls. The verb-suffixed names are unmistakable; bare `put` and
# `delete` are not, which is why the file is gated on the class name and the
# rows go through `path_only`.
_REST_TEMPLATE_RE = re.compile(
    r"(?:(?:\b[A-Za-z_]\w*|\))\s*\.\s*"
    r"(getForObject|getForEntity|postForObject|postForEntity|patchForObject|exchange)"
    r"|\b\w*[Tt]emplate\w*(?:\(\s*\))?\s*\.\s*(put|delete))"
    r"\s*\("
)

# RequestEntity.get(url) / RequestEntity.method(HttpMethod.X, url): a request
# whose builder head names its verb and URL.
_REQUEST_ENTITY_RE = re.compile(
    r"\bRequestEntity\s*\.\s*(get|post|put|delete|patch|head|options|method)\s*\("
)
_ENTITY_ASSIGN_RE = re.compile(r"(?<![.\w$])([A-Za-z_]\w*)\s*=(?!=)\s*(?=RequestEntity\s*\.)")

# A WebClient / RestClient request: the verb step, then the `.uri(` naming the URL.
_SPRING_REQUEST_RE = re.compile(
    r"\.\s*(?:(?P<verb>get|post|put|delete|patch|head|options)\s*\(\s*\)"
    r"|method\s*\(\s*(?P<method>[\w.]+)\s*\))\s*\.\s*uri\s*\("
)

# Apache HttpClient 4 and 5: the request class names the verb.
_APACHE_REQUEST_RE = re.compile(r"\bnew\s+Http(Get|Post|Put|Delete|Patch|Head|Options)\s*\(")

# A constructor call, whose arguments may carry a helper's base URL.
_NEW_RE = re.compile(r"\bnew\s+([A-Z]\w*)\s*\(")
_CLASS_RE = re.compile(r"\bclass\s+([A-Z]\w*)")
# `this.base = base;` or `base = base;` in a constructor body.
_FIELD_FROM_PARAM_RE = re.compile(
    r"(?<![\w$])(?:this\s*\.\s*)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;"
)
# Mount key of the URL a `new C(...)` call passes: class, arity, position.
_CTOR_KEY = "java-ctor-url:"
# A generic argument list or a parameter annotation, dropped before parameters split.
_GENERIC_RE = re.compile(r"<[^<>]*>")
_PARAM_ANNOTATION_RE = re.compile(r"@\w+(?:\s*\([^()]*\))?")
# Words that precede a name without declaring it.
_NOT_TYPES = frozenset({"return", "throw", "case", "else", "new", "yield", "assert"})


def feign_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _REQUEST_LINE_RE.finditer(content):
        verb = m.group(1)
        if verb.lower() not in VERBS:
            continue
        yield ClientCallMatch(
            client="feign",
            # The annotation holds the path unquoted; the resolver reads a
            # literal, so hand it one.
            url=f'"{m.group(2).strip()}"',
            offset=m.start(),
            method=verb.upper(),
            origin=ContractOrigin.CLIENT_CALL,
        )


def _chain_steps(content: str, start: int) -> Iterator[tuple[str, int]]:
    """``(step name, its '(' offset)`` for each ``.name(...)`` from *start*.

    Stops at the first character that is not part of the chain (the statement's
    ``;``, a comma, a closing paren) and after ``.build()``, so a verb belonging
    to a neighbouring call is never read as this request's terminal.
    """
    i = start
    limit = min(len(content), start + _CHAIN_LIMIT)
    while i < limit:
        if content[i].isspace():
            i += 1
            continue
        m = _CHAIN_STEP_RE.match(content, i)
        if m is None:
            return
        close = match_paren(content, m.end() - 1)
        if close < 0:
            return
        yield m.group(1), m.end() - 1
        if m.group(1) == "build":
            return
        i = close + 1


def java_net_http_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _NEW_BUILDER_RE.finditer(content):
        paren = m.end() - 1
        close = match_paren(content, paren)
        args = call_arguments(content, paren, close) if close >= 0 else None
        if args is None:
            continue
        # newBuilder(uri); the two-argument copy form carries a request, not a URL.
        url = args[0] if len(args) == 1 else None
        method: str | None = None
        for name, step_paren in _chain_steps(content, close + 1):
            if name == "uri" and url is None:
                inner = call_arguments(content, step_paren)
                if inner is not None and len(inner) == 1:
                    url = inner[0]
            elif name.isupper() and name.lower() in VERBS:
                method = name
            elif name == "method":
                inner = call_arguments(content, step_paren)
                if inner:
                    method = method_from_argument(inner[0])
        if url is None or method is None:
            continue  # no URL, or a verb only the caller knows
        yield ClientCallMatch(
            client="java-net-http",
            url=url,
            offset=m.start(),
            method=method,
            origin=ContractOrigin.CLIENT_CALL,
        )


def resttemplate_calls(content: str) -> Iterator[ClientCallMatch]:
    if "RestTemplate" not in content:
        return
    entities: dict[str, tuple[str, str]] | None = None
    for m in _REST_TEMPLATE_RE.finditer(content):
        callee = m.group(1) or m.group(2)
        paren = m.end() - 1
        args = call_arguments(content, paren)
        if not args:
            continue
        method = None
        url = args[0]
        if callee == "exchange":
            if len(args) < 2:
                continue
            method = method_from_argument(args[1])
            if method is None:
                # exchange(entity, type): the RequestEntity names verb and URL.
                if entities is None:
                    entities = _request_entities(content)
                entity = _request_entity(url.strip()) or entities.get(url.strip())
                if entity is None:
                    continue  # a verb held in a variable is not one we know
                method, url = entity
        yield ClientCallMatch(
            client="resttemplate",
            url=url,
            offset=m.start(),
            callee=callee,
            method=method,
            origin=ContractOrigin.NAME_HEURISTIC,
        )


def _request_entity(text: str, at: int = 0) -> tuple[str, str] | None:
    """``(verb, URL text)`` of the ``RequestEntity`` builder starting at *at*, else ``None``."""
    m = _REQUEST_ENTITY_RE.match(text, at)
    if m is None:
        return None
    args = call_arguments(text, m.end() - 1)
    if not args:
        return None
    if m.group(1) == "method":
        verb = method_from_argument(args[0]) if len(args) == 2 else None
        return (verb, args[1]) if verb else None
    return (m.group(1).upper(), args[0]) if len(args) == 1 else None


def _request_entities(content: str) -> dict[str, tuple[str, str]]:
    """Names the file assigns exactly once, to a ``RequestEntity`` builder."""
    out: dict[str, tuple[str, str]] = {}
    for m in _ENTITY_ASSIGN_RE.finditer(content):
        name = m.group(1)
        entity = _request_entity(content, m.end())
        assigned = re.findall(rf"(?<![.\w$]){name}\s*=(?!=)", content)
        if entity is not None and len(assigned) == 1:
            out[name] = entity
    return out


def spring_request_calls(content: str) -> Iterator[ClientCallMatch]:
    """``WebClient`` and ``RestClient`` requests: a verb step, then ``.uri(url, ...)``.

    A ``.uri(builder -> ...)`` function names no URL this can read and is
    refused when it is resolved.
    """
    for m in _SPRING_REQUEST_RE.finditer(content):
        verb = m.group("verb")
        method = verb.upper() if verb else method_from_argument(m.group("method"))
        args = call_arguments(content, m.end() - 1)
        if method is None or not args:
            continue
        yield ClientCallMatch(
            client="webclient",
            url=args[0],
            offset=m.start(),
            method=method,
            origin=ContractOrigin.CLIENT_CALL,
        )


def apache_calls(content: str) -> Iterator[ClientCallMatch]:
    """Apache HttpClient requests: ``new HttpPost(url)`` and its siblings."""
    for m in _APACHE_REQUEST_RE.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if not args or len(args) != 1:
            continue
        yield ClientCallMatch(
            client="apache-httpclient",
            url=args[0],
            offset=m.start(),
            method=m.group(1).upper(),
            origin=ContractOrigin.CLIENT_CALL,
        )


def ctor_url_arguments(content: str) -> dict[str, str]:
    """The absolute URLs this file's ``new C(...)`` calls pass, keyed by class and position.

    Ceiling: only a URL a file resolves is recorded, so another call site
    passing a URL held in a variable does not make the key ambiguous. Two
    sites passing different URLs do, and the merge drops the key.
    """
    out: dict[str, str] = {}
    constants: dict[str, str] | None = None
    for m in _NEW_RE.finditer(content):
        args = call_arguments(content, m.end() - 1) or []
        for i, arg in enumerate(args):
            if "://" not in arg and not arg.strip().isidentifier():
                continue  # neither a URL literal nor a name that could hold one
            if constants is None:
                constants = string_constants(content, JAVA_SYNTAX)
            value = resolve_string(arg, JAVA_SYNTAX, constants)
            if value is not None and "://" in value and '"' not in value:
                out[_ctor_key(m.group(1), len(args), i)] = value
    return out


def _ctor_key(cls: str, arity: int, position: int) -> str:
    # The arity keeps an overload's arguments apart from another's.
    return f"{_CTOR_KEY}{cls}/{arity}:{position}"


def _param_names(text: str) -> list[str]:
    """The names a parameter list declares, generics and annotations dropped."""
    text = _PARAM_ANNOTATION_RE.sub(" ", text)
    while True:
        stripped = _GENERIC_RE.sub(" ", text)
        if stripped == text:
            break
        text = stripped
    return [p.split()[-1].lstrip("*") for p in text.split(",") if p.split()]


def _ctor_params(content: str, cls: str) -> Iterator[tuple[list[str], int, int, int]]:
    """``(parameter names, parameters start, body start, body end)`` per constructor of *cls*.

    A declaration opens its line or follows an access modifier, so a call
    (``new Cls(``, ``x = Cls(``) is not read as one.
    """
    decl_re = rf"(?m)(?:^[ \t]*|\b(?:public|protected|private)\s+){cls}\s*\("
    for decl in re.finditer(decl_re, content):
        close = match_paren(content, decl.end() - 1)
        if close < 0:
            continue
        brace = re.match(r"\s*(?:throws[^{;]*)?\{", content[close + 1 : close + 200])
        if brace is None:
            continue
        start = close + brace.end()
        end = match_paren(content, start, closer="}")
        params = _param_names(content[decl.end() : close])
        yield params, decl.end(), start, end if end > 0 else start


def _ctor_bases(content: str, mounts: Mapping[str, str]) -> dict[str, str]:
    """Fields a constructor here sets from a parameter a ``new`` call fills with a URL.

    The value is the URL as a Java literal. A field assigned anywhere else in
    the file (an initialiser, a setter) is left out: which value reaches the
    call is then not settled.
    """
    out: dict[str, str] = {}
    for cls in dict.fromkeys(_CLASS_RE.findall(content)):
        for params, params_start, start, end in _ctor_params(content, cls):
            for a in _FIELD_FROM_PARAM_RE.finditer(content, start, end):
                field, param = a.group(1), a.group(2)
                if param not in params:
                    continue
                value = mounts.get(_ctor_key(cls, len(params), params.index(param)))
                if value is not None and _settled_field(content, field, params_start, start):
                    out[field] = f'"{value}"'
    return out


def _settled_field(content: str, field: str, ctor_start: int, ctor_body: int) -> bool:
    """Whether *field* is written once, and no parameter or local elsewhere shadows it.

    The constructor's own parameter list is the one place the name may also
    be declared: a ``base`` parameter of another method would be any URL.
    """
    if len(re.findall(rf"(?<![\w$]){field}\s*=(?!=)", content)) != 1:
        return False
    declared = [
        m.start()
        for m in re.finditer(rf"(\w+|[>\]])\s+{field}\s*[,)=;:]", content)
        if m.group(1) not in _NOT_TYPES and not ctor_start <= m.start() < ctor_body
    ]
    return len(declared) == 1  # the field's own declaration


def _with_bases(rows: list[ClientCallMatch], bases: dict[str, str]) -> list[ClientCallMatch]:
    """*rows*, each URL led by a constructor-set base field put onto that base's literal."""
    out = []
    for row in rows:
        pieces = split_top_level(row.url, "+")
        head = pieces[0].strip()
        if head in bases:
            url = " + ".join([bases[head], *pieces[1:]])
            row = replace(row, url=url, origin=ContractOrigin.DATAFLOW)
        out.append(row)
    return out


class JavaClientsDialect:
    name = "java-clients"
    extensions = JAVA | KOTLIN
    requires = (
        "@RequestLine",
        "HttpRequest",
        "RestTemplate",
        "WebClient",
        "RestClient",
        "org.apache.http",
        "org.apache.hc",
    )
    # The repo-wide pass reads constructor calls that pass a URL literal on.
    mount_requires = ('"http://', '"https://')

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        return ctor_url_arguments(ctx.content) if ctx.suffix in JAVA else {}

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        named = [*feign_calls(content), *java_net_http_calls(content)]
        if ctx.has_any(("WebClient", "RestClient")):
            named.extend(spring_request_calls(content))
        if ctx.has_any(("org.apache.http", "org.apache.hc")):
            named.extend(apache_calls(content))
        # `.put`/`.delete` on any receiver could be a collection, so a URL with
        # no slash in it is a key rather than a route.
        ambiguous = list(resttemplate_calls(content))
        if not named and not ambiguous:
            return []
        syntax = syntax_for_suffix(ctx.suffix) or JAVA_SYNTAX
        constants = string_constants(content, syntax)
        if ctx.suffix in JAVA and ctx.mounts:
            bases = _ctor_bases(content, ctx.mounts)
            if bases:
                named, ambiguous = _with_bases(named, bases), _with_bases(ambiguous, bases)
        return [
            *consumer_contracts(ctx, named, syntax, constants=constants),
            *consumer_contracts(ctx, ambiguous, syntax, constants=constants, path_only=True),
        ]


# A literal argument of a Spring annotation: `key = "value"`, or the positional
# first argument, which is `value`.
_ANNOTATION_STRING_RE = re.compile(
    r'(?:,\s*(?P<key>\w+)\s*=\s*|\(\s*(?:(?P<first>\w+)\s*=\s*)?)"(?P<value>[^"\\\n]*)"\s*(?=[,)])'
)

# A controller in the same file: `@HttpExchange` there declares a route served.
# The guard is per file; a shared interface a controller elsewhere implements
# still reads as a client.
_CONTROLLER_RE = re.compile(r"@(?:Rest)?Controller\b")

# A host the service-name map can look up: a bare service id, no placeholder.
_SERVICE_ID_RE = re.compile(r"[\w.-]+")


def _top_level(args: str) -> str:
    """*args* with every nested ``(...)``, ``{...}`` and ``[...]`` blanked, offsets kept.

    A nested annotation's ``path`` or ``url`` (``configuration = @Cfg(path = ...)``)
    is not the client's.
    """
    out = list(args)
    depth = 0
    quoted = False
    for i, ch in enumerate(args):
        if ch == '"' and (i == 0 or args[i - 1] != "\\"):
            quoted = not quoted
        elif not quoted and ch in "([{":
            depth += 1
        elif not quoted and ch in ")]}":
            depth -= 1
            if depth >= 1:
                out[i] = " "
            continue
        if depth > 1:
            out[i] = " "
    return "".join(out)


def _annotation_strings(args: str) -> dict[str, str]:
    """The literal arguments of an annotation's *args*, by key."""
    out: dict[str, str] = {}
    for m in _ANNOTATION_STRING_RE.finditer(args):
        out.setdefault(m.group("key") or m.group("first") or "value", m.group("value"))
    return out


def _names_key(args: str, key: str) -> bool:
    """Whether *args* passes *key* at all, literal or not."""
    return re.search(rf"[(,]\s*{key}\s*=", args) is not None


def _feign_base(args: str) -> tuple[str, str] | None:
    """``(base URL, path prefix)`` a ``@FeignClient`` declares, ``None`` when unreadable.

    ``url`` is where the client sends, literal or a ``${property}`` placeholder
    the base resolver reads like any other. With no ``url`` the client calls
    the service its ``name`` (or ``value``) registers under, which becomes the
    host so the service-name map can place it; a placeholder name, or a ``url``
    held in a constant, leaves the call a bare path. A ``path`` this cannot
    read leaves every route under the client unknown.
    """
    args = _top_level(args)
    strings = _annotation_strings(args)
    prefix = strings.get("path", "")
    if "${" in prefix or ("path" not in strings and _names_key(args, "path")):
        return None
    return _feign_host(args, strings), prefix


def _feign_host(args: str, strings: dict[str, str]) -> str:
    """The base URL a Feign client sends to: its ``url``, else its service name, else ``""``."""
    if "url" in strings:
        return strings["url"].rstrip("/")
    name = strings.get("name") or strings.get("value") or ""
    if _names_key(args, "url") or not _SERVICE_ID_RE.fullmatch(name):
        return ""
    return f"http://{name.lower()}"


def _composed_paths(
    class_paths: list[tuple[int, int, tuple[str, ...] | None]], route: RouteMatch, prefix: str
) -> Iterator[str]:
    """Each concrete path *route* reaches under its type prefixes and the client *prefix*."""
    for type_prefix in spring_route_prefixes(class_paths, route.offset) or ():
        path = compose_prefix(prefix, compose_prefix(type_prefix, route.path or ""))
        if path.strip("/"):
            yield path


def _interface_rows(
    routes: Iterator[RouteMatch],
    class_paths: list[tuple[int, int, tuple[str, ...] | None]],
    base_at: Callable[[int], tuple[str, str] | None],
    *,
    client: str,
    unrestricted: str | None,
) -> Iterator[ClientCallMatch]:
    """Rows for a client interface's *routes*, each composed onto its type prefix and base.

    *base_at* gives the ``(base URL, path prefix)`` of the client around an
    offset. *unrestricted* is the verb the client sends for a mapping that
    names none; ``None`` refuses such a mapping.
    """
    for route in routes:
        base = None if route.path is None else base_at(route.offset)
        verb = unrestricted if route.verb == "ALL" else route.verb
        if base is None or verb is None or verb.lower() not in VERBS:
            continue
        url_base, prefix = base
        for path in _composed_paths(class_paths, route, prefix):
            yield ClientCallMatch(
                client=client,
                # The resolver reads a literal, so hand it one.
                url=f'"{url_base}/{path.lstrip("/")}"',
                offset=route.offset,
                method=verb,
                origin=ContractOrigin.CLIENT_CALL,
            )


def feign_interface_calls(content: str, annotations: set[int]) -> Iterator[ClientCallMatch]:
    """Calls an ``@FeignClient`` interface declares with Spring MVC annotations."""
    clients = spring_feign_clients(content, annotations)
    if not clients:
        return

    def base_at(offset: int) -> tuple[str, str] | None:
        inner = [args for start, end, args in clients if start < offset < end]
        return _feign_base(inner[-1]) if inner else None

    yield from _interface_rows(
        spring_routes(content, annotations, feign=True),
        spring_class_paths(content, annotations),
        base_at,
        client="feign",
        unrestricted="GET",  # Feign sends a mapping that names no verb as a GET
    )


def http_exchange_calls(content: str, annotations: set[int]) -> Iterator[ClientCallMatch]:
    """Calls a Spring 6 ``@HttpExchange`` interface declares.

    The base URL is set where the proxy is built, out of this file's sight, so
    each row is a path. A file holding a controller is skipped: there the same
    annotations declare routes served, not calls.
    """
    if _CONTROLLER_RE.search(content):
        return
    yield from _interface_rows(
        spring_routes(content, annotations, SPRING_EXCHANGE),
        spring_class_paths(content, annotations, SPRING_EXCHANGE),
        lambda _offset: ("", ""),
        client="http-exchange",
        unrestricted=None,
    )


class SpringClientsDialect:
    name = "spring-clients"
    extensions = JAVA | KOTLIN
    requires = (
        "@FeignClient",
        "@HttpExchange",
        "@GetExchange",
        "@PostExchange",
        "@PutExchange",
        "@DeleteExchange",
        "@PatchExchange",
    )

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        annotations = micronaut_annotations(content)
        rows = [
            *feign_interface_calls(content, annotations),
            *http_exchange_calls(content, annotations),
        ]
        if not rows:
            return []
        # The URL text is built here, not read from the file, so it resolves as
        # a plain literal whatever the file's language.
        return consumer_contracts(ctx, rows, JAVA_SYNTAX)


def _microprofile_base(args: str) -> str:
    """The base a ``@RegisterRestClient`` names in ``baseUri``, else ``""``.

    A ``configKey`` names configuration this file cannot read, so the call is
    a bare path.
    """
    return _annotation_strings(_top_level(args)).get("baseUri", "").rstrip("/")


def microprofile_client_calls(content: str, annotations: set[int]) -> Iterator[ClientCallMatch]:
    """Calls a MicroProfile ``@RegisterRestClient`` interface declares with JAX-RS annotations."""
    clients = microprofile_rest_clients(content, annotations)
    if not clients:
        return
    class_paths = [
        (start, end, None if prefix is None else (prefix.rstrip("/"),))
        for start, end, _, prefix in clients
    ]

    def base_at(offset: int) -> tuple[str, str] | None:
        inner = [args for start, end, args, _ in clients if start < offset < end]
        return (_microprofile_base(inner[-1]), "") if inner else None

    yield from _interface_rows(
        jaxrs_routes(content),
        class_paths,
        base_at,
        client="microprofile",
        unrestricted=None,
    )


class MicroProfileClientsDialect:
    name = "microprofile-clients"
    extensions = JAVA | KOTLIN
    requires = ("@RegisterRestClient",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        rows = list(microprofile_client_calls(content, micronaut_annotations(content)))
        return consumer_contracts(ctx, rows, JAVA_SYNTAX) if rows else []

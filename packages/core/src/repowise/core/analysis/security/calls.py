"""Call-based findings: eval/exec, child_process exec and TLS verification turned off."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable

from repowise.core.pyparse import parse_python

from .credentials import _snippet
from .masking import source_lines
from .patterns import _CALL_PATTERNS

# ``exec`` is not a global in JavaScript; the name belongs to
# ``RegExp.prototype.exec``. The receiver-chain prefix above therefore matches
# ``re.exec(str)``, ``/x/.exec(s)`` and ``pattern.exec(xml)`` — idiomatic parsing
# code, reported at ``high``. Measured on a 17-repo TypeScript corpus, every
# ``exec_call`` hit outside Python was a regex match and none was a process
# spawn, so the kind was pure noise on those languages.
#
# The dangerous call in JavaScript comes from ``child_process``, so only a call
# through a name bound to that module counts: a namespace (``cp.exec(``), a named
# import (``exec(`` after ``import { exec } from "child_process"``) or the
# ``require`` result itself. Only ``exec`` and ``execSync`` run a shell;
# ``execFile`` passes its arguments as argv, like a Python ``subprocess.run``
# without ``shell=True``, which does not fire either. A file's own function named ``exec``, or a regex's
# ``.exec`` next to an unrelated ``spawn`` import, is not a shell. ``eval`` needs
# no such gate: it is a genuine global there.
_CP_MODULE = r"""['"](?:node:)?child_process['"]"""
_JS_NAME = r"[A-Za-z_$][\w$]*"
_CP_NAMESPACE = re.compile(
    rf"import\s+(?:\*\s*as\s+)?({_JS_NAME})\s*(?:,\s*\{{[^}}]*\}}\s*)?from\s*{_CP_MODULE}"
    rf"|(?:const|let|var)\s+({_JS_NAME})\s*=\s*require\(\s*{_CP_MODULE}\s*\)"
)
_CP_NAMED = re.compile(
    rf"import\s*(?:{_JS_NAME}\s*,\s*)?\{{([^}}]*)\}}\s*from\s*{_CP_MODULE}"
    rf"|(?:const|let|var)\s*\{{([^}}]*)\}}\s*=\s*require\(\s*{_CP_MODULE}\s*\)"
)
_CP_REQUIRE_CALL = re.compile(
    rf"require\(\s*{_CP_MODULE}\s*\)\s*\.\s*exec(?:Sync)?\s*\("
)
_EXEC_EXPORT = re.compile(r"exec(?:Sync)?")


def _child_process_exec_calls(source: str, masked: str) -> list[int]:
    """Offsets of ``child_process`` exec-family calls, found through the file's own bindings.

    Imports are read from raw source because the module name is a string
    literal, which masking blanks; calls are read from masked source, so a
    comment or string naming one does not count.
    """
    namespaces = {n for m in _CP_NAMESPACE.finditer(source) for n in m.groups() if n}
    functions: set[str] = set()
    for m in _CP_NAMED.finditer(source):
        for item in (m.group(1) or m.group(2)).split(","):
            # ``exec as run`` (import) and ``exec: run`` (destructuring) rename.
            parts = re.split(r"\s+as\s+|\s*:\s*", item.strip().removeprefix("type "))
            if parts[0] and _EXEC_EXPORT.fullmatch(parts[0]):
                functions.add(parts[-1].strip())
    offsets = [m.start() for m in _CP_REQUIRE_CALL.finditer(source) if not masked[m.start()].isspace()]
    names = [re.escape(n) for n in namespaces]
    if names:
        receiver = rf"(?<![\w$.])(?:{'|'.join(names)})\s*\.\s*exec(?:Sync)?\s*\("
        offsets += [m.start() for m in re.finditer(receiver, masked)]
    if functions:
        bare = rf"(?<![\w$.])(?:{'|'.join(map(re.escape, functions))})\s*\("
        offsets += [m.start() for m in re.finditer(bare, masked)]
    return sorted(offsets)


# ``verify=False`` turns TLS off only when an HTTP client receives it, so the
# file must import an HTTP client library, and a call through a plain name must
# go through that import or a value built from it (``s = requests.Session()``).
# Any other function is free to name a keyword ``verify``. Ceiling: a project's
# own wrapper that forwards ``verify``, imported from another module, is not seen.
_HTTP_LIBS = frozenset(
    {"requests", "httpx", "aiohttp", "urllib3", "boto3", "botocore", "aiobotocore", "aioboto3"}
)
_HTTP_CALLS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "request",
        "send",
        "stream",
        "urlopen",
        "Client",
        "AsyncClient",
        "Session",
        "HTTPTransport",
        "AsyncHTTPTransport",
        "client",
        "resource",
    }
)


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a name or attribute chain, else ``""``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)):
        return f"{base}.{node.attr}"
    return ""


def _root(node: ast.AST) -> ast.AST:
    """The expression a chain of calls and attributes starts from."""
    while isinstance(node, (ast.Call, ast.Attribute, ast.Await)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node


def _http_bindings(tree: ast.AST) -> set[str]:
    """Names and ``self.x`` targets bound to an HTTP client library or a value built from it."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _HTTP_LIBS:
                    bound.add(alias.asname or alias.name.split(".")[0])
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".")[0] in _HTTP_LIBS
        ):
            bound.update(alias.asname or alias.name for alias in node.names)
    if not bound:
        return bound
    # Twice, so a value built from a bound value (``c = s.client()``) is bound too.
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                pairs = [(node.target, node.value)]
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                pairs = [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars]
            else:
                continue
            for target, value in pairs:
                if _dotted(_root(value)) in bound and (name := _dotted(target)):
                    bound.add(name)
    return bound


def _tls_verify_off_lines(source: str) -> set[int] | None:
    """Lines where ``verify=False`` reaches an HTTP client; None when *source* does not parse."""
    try:
        tree = parse_python(source)
    except SyntaxError:
        return None
    bound = _http_bindings(tree)
    if not bound:
        return set()
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                name, receiver = func.attr, _root(func.value)
                # A plain name must be bound; ``self.x`` and call results are
                # taken on trust once the file uses an HTTP client library.
                reaches = (
                    _dotted(receiver) in bound
                    or _dotted(func.value) in bound
                    or not isinstance(receiver, ast.Name)
                    or receiver.id in ("self", "cls")
                )
            elif isinstance(func, ast.Name):
                name, reaches = func.id, func.id in bound
            else:
                continue
            if name in _HTTP_CALLS and reaches:
                found.update(
                    kw.lineno
                    for kw in node.keywords
                    if kw.arg == "verify"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                )
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value is False
        ):
            # ``session.verify = False``, or a ``verify = False`` setting handed
            # to a client later.
            found.update(
                target.lineno
                for target in node.targets
                if (isinstance(target, ast.Attribute) and target.attr == "verify")
                or (isinstance(target, ast.Name) and target.id == "verify")
            )
    return found



def _java_script_engine_eval_offsets(source: str, masked: str) -> list[int]:
    """Find Java ScriptEngine.eval calls using syntactic variable scopes."""
    from repowise.core.analysis.health.dataflow.parsing import parse_source

    source_bytes = source.encode("utf-8")
    parsed = parse_source("Source.java", "java", source_bytes)
    if parsed is None:
        return []

    root, _ = parsed
    offsets = []

    def text(node):
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8")

    def char_offset(byte_offset):
        return len(source_bytes[:byte_offset].decode("utf-8"))

    has_script_engine_import = any(
        child.type == "import_declaration"
        and text(child).strip() in {
            "import javax.script.ScriptEngine;",
            "import javax.script.*;",
        }
        for child in root.named_children
    )

    def is_engine_type(node):
        if node is None:
            return False
        type_name = text(node)
        return (
            type_name == "javax.script.ScriptEngine"
            or (type_name == "ScriptEngine" and has_script_engine_import)
        )

    def register(node, bindings):
        typ = node.child_by_field_name("type")
        if typ is None:
            return

        if node.type in {
            "formal_parameter",
            "enhanced_for_statement",
            "resource",
        }:
            name = node.child_by_field_name("name")
            if name is not None:
                bindings[text(name)] = is_engine_type(typ)

        elif node.type in {
            "local_variable_declaration",
            "field_declaration",
            "constant_declaration",
        }:
            for child in node.named_children:
                if child.type == "variable_declarator":
                    name = child.child_by_field_name("name")
                    if name is not None:
                        is_engine = is_engine_type(typ)

                        if text(typ) == "var":
                            value = child.child_by_field_name("value")
                            if value is not None and value.type == "method_invocation":
                                method = value.child_by_field_name("name")
                                receiver = value.child_by_field_name("object")

                                if (
                                    method is not None
                                    and receiver is not None
                                    and text(method) == "getEngineByName"
                                    and receiver.type == "object_creation_expression"
                                    and any(
                                        child.type == "type_identifier"
                                        and text(child) == "ScriptEngineManager"
                                        for child in receiver.named_children
                                    )
                                ):
                                    is_engine = True

                        bindings[text(name)] = is_engine

    def visit(node, bindings, class_fields):
        if node.type in {
            "class_declaration",
            "enum_declaration",
            "record_declaration",
            "interface_declaration",
        }:
            class_bindings = {}
            body = node.child_by_field_name("body")

            if body is not None:
                for child in body.named_children:
                    if child.type in {"field_declaration", "constant_declaration"}:
                        register(child, class_bindings)
                    elif child.type == "enum_body_declarations":
                        for member in child.named_children:
                            if member.type == "field_declaration":
                                register(member, class_bindings)

            if node.type == "record_declaration":
                parameters = node.child_by_field_name("parameters")
                if parameters is not None:
                    for parameter in parameters.named_children:
                        register(parameter, class_bindings)

            bindings = {**bindings, **class_bindings}
            class_fields = class_bindings

        if node.type in {"method_declaration", "constructor_declaration"}:
            bindings = bindings.copy()
            parameters = node.child_by_field_name("parameters")

            if parameters is not None:
                for parameter in parameters.named_children:
                    register(parameter, bindings)

        if node.type == "block":
            bindings = bindings.copy()

        if node.type == "local_variable_declaration":
            register(node, bindings)

        if node.type == "enhanced_for_statement":
            bindings = bindings.copy()
            register(node, bindings)

        if node.type == "try_with_resources_statement":
            bindings = bindings.copy()
            resources = node.child_by_field_name("resources")
            if resources is not None:
                for resource in resources.named_children:
                    if resource.type == "resource":
                        register(resource, bindings)

        if node.type == "method_invocation":
            name = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")

            if (
                name is not None
                and obj is not None
                and text(name) == "eval"
                and (
                    (
                        obj.type == "identifier"
                        and bindings.get(text(obj), False)
                    )
                    or (
                        obj.type == "field_access"
                        and text(obj.child_by_field_name("object")) == "this"
                        and class_fields.get(
                            text(obj.child_by_field_name("field")), False
                        )
                    )
                )
            ):
                offset = char_offset(name.start_byte)
                if masked[offset:offset + 4] == "eval":
                    offsets.append(char_offset(obj.start_byte))

        for child in node.named_children:
            visit(child, bindings, class_fields)

    visit(root, {}, {})
    return sorted(set(offsets))


def _call_findings(file_path: str, source: str, masked: Callable[[], str]) -> list[dict]:
    """Find executable eval/exec calls with AST or bounded lexical fallback.

    *masked* returns the source with comments and strings blanked, built on
    first use and shared with the caller.
    """
    is_python = file_path.lower().endswith((".py", ".pyi"))
    # Masking only blanks characters, so every call found below names ``eval``
    # or ``exec`` in the source, and outside Python ``exec`` counts only through
    # ``child_process``. ``ast`` folds non-ASCII identifiers (NFKC), so such
    # Python files still parse.
    words = ("eval", "exec") if is_python else ("eval", "child_process")
    if not any(word in source for word in words) and (not is_python or source.isascii()):
        return []
    if is_python:
        try:
            tree = parse_python(source)
        except SyntaxError:
            pass
        else:
            lines = source_lines(source)
            findings = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                else:
                    continue
                if call_name not in {"eval", "exec"}:
                    continue
                findings.append(
                    {
                        "kind": f"{call_name}_call",
                        "severity": "high",
                        # ast also breaks lines on a lone ``\r``, which git does
                        # not; such a line number can outrun the split.
                        "snippet": _snippet(lines[node.lineno - 1])
                        if node.lineno <= len(lines)
                        else "",
                        "line": node.lineno,
                    }
                )
            return findings

    masked_source = masked()
    lines = source_lines(source)
    findings = []

    def add(kind: str, severity: str, offset: int) -> None:
        lineno = source.count("\n", 0, offset) + 1
        snippet = _snippet(lines[lineno - 1]) if lineno <= len(lines) else ""
        findings.append({"kind": kind, "severity": severity, "snippet": snippet, "line": lineno})

    for pattern, kind, severity in _CALL_PATTERNS:
        if kind == "eval_call" and file_path.lower().endswith((".java", ".kt", ".kts")):
            continue  # eval is not a general Java/Kotlin code execution primitive
        if kind == "exec_call" and not is_python:
            continue  # handled below, gated on child_process
        for match in pattern.finditer(masked_source):
            add(kind, severity, match.start())

    if file_path.lower().endswith(".java"):
        for offset in _java_script_engine_eval_offsets(source, masked_source):
            add("eval_call", "high", offset)

    if not is_python:
        for offset in _child_process_exec_calls(source, masked_source):
            add("exec_call", "high", offset)

    return findings

"""Literal service addresses in Terraform resources, without evaluating HCL.

Read jsonencode ECS environments, Lambda variable maps, Cloud Run env blocks and
Kubernetes Service metadata/selectors. Expressions, interpolation, heredocs and
external-name Services are refused. DNS names resolve through the existing host
map/repo aliases; suffixes are not guessed. This is a bounded literal reader, not
an HCL interpreter (including for_each/dynamic blocks or JSON-encoded strings).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator

from .base import ENV, Claim, ServiceNameSource, SourceFile, host_of

_TOKEN = re.compile(
    r"(?P<heredoc><<-?(?P<tag>[A-Za-z_]\w*)[^\n]*\n.*?^[ \t]*(?P=tag)[ \t]*$)"
    r"|(?P<comment>\#[^\n]*|//[^\n]*|/\*.*?(?:\*/|\Z))"
    r'|(?P<string>"(?:\\.|[^"\\])*")'
    r"|(?P<space>[^\S\n]+)|(?P<token>\n|[A-Za-z_][\w-]*|<<[^\n]*|\S)",
    re.MULTILINE | re.DOTALL,
)
_PAIRS = {"{": "}", "[": "]", "(": ")"}
_TYPES = {
    "aws_ecs_task_definition",
    "aws_lambda_function",
    "google_cloud_run_service",
    "google_cloud_run_v2_service",
    "kubernetes_service",
    "kubernetes_service_v1",
}


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(text):
        if match[0] == '"':
            return []  # An unfinished string must not expose its contents as code.
        if match.lastgroup in {"comment", "space"}:
            continue
        if match.lastgroup == "heredoc":
            tokens.append("<unsupported>")
        elif match[0].startswith("<<"):
            return []  # Unterminated heredoc: its contents must never become code.
        else:
            tokens.append(match[0])
    return tokens


def _close(tokens: list[str], start: int) -> int:
    stack = [_PAIRS[tokens[start]]]
    for i in range(start + 1, len(tokens)):
        token = tokens[i]
        if token in _PAIRS:
            stack.append(_PAIRS[token])
            if len(stack) > 32:
                return -1
        elif token in _PAIRS.values():
            if token != stack.pop():
                return -1
            if not stack:
                return i
    return -1


def _literal(token: str) -> str | None:
    if not token.startswith('"'):
        return None
    try:
        value = json.loads(token)
    except ValueError:
        return None
    return value if "${" not in value and "%{" not in value else None


def _expression_end(tokens: list[str], start: int) -> int:
    i = start
    while i < len(tokens) and tokens[i] not in {"\n", ","}:
        if tokens[i] in _PAIRS:
            i = _close(tokens, i)
            if i < 0:
                return -1
        i += 1
    return i


def _fields(tokens: list[str]) -> Iterator[tuple[str, list[str]]]:
    i = 0
    while i < len(tokens):
        if tokens[i] in _PAIRS:
            end = _close(tokens, i)
            if end < 0:
                return
            i = end + 1
        elif i + 1 < len(tokens) and tokens[i + 1] == "=":
            key = _literal(tokens[i]) if tokens[i].startswith('"') else tokens[i]
            start = i + 2
            i = _expression_end(tokens, start)
            if i < 0:
                return
            if key:
                yield key, tokens[start:i]
        else:
            i += 1


def _literals(tokens: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    repeated: set[str] = set()
    seen: set[str] = set()
    for key, expr in _fields(tokens):
        if key in seen:
            repeated.add(key)
        seen.add(key)
        value = _literal(expr[0]) if len(expr) == 1 else None
        if value is not None:
            out[key] = value
    return {key: value for key, value in out.items() if key not in repeated}


def _groups(tokens: list[str], name: str) -> Iterator[list[str]]:
    for i, token in enumerate(tokens):
        if token == name and i + 1 < len(tokens) and tokens[i + 1] == "{":
            end = _close(tokens, i + 1)
            if end >= 0:
                yield tokens[i + 2 : end]


def _env_claims(values: Iterable[tuple[str, str]]) -> Iterator[Claim]:
    for key, value in values:
        host = host_of(key, value)
        if host:
            yield Claim(name=key.lower(), kind=ENV, ref=host)


def _named_envs(tokens: list[str]) -> Iterator[Claim]:
    fields = _literals(tokens)
    if "name" in fields and "value" in fields:
        yield from _env_claims([(fields["name"], fields["value"])])


def _object_list(tokens: list[str]) -> Iterator[list[str]]:
    if tokens[:1] != ["["] or _close(tokens, 0) != len(tokens) - 1:
        return
    i = 1
    while i < len(tokens) - 1:
        if tokens[i] in {"\n", ","}:
            i += 1
            continue
        if tokens[i] != "{" or (end := _close(tokens, i)) < 0:
            return
        yield tokens[i + 1 : end]
        i = end + 1


def _container_environment(tokens: list[str]) -> Iterator[Claim]:
    for field, value in _fields(tokens):
        if field != "environment":
            continue
        for item in _object_list(value):
            yield from _named_envs(item)


def _ecs(tokens: list[str]) -> Iterator[Claim]:
    for key, expr in _fields(tokens):
        if (
            key != "container_definitions"
            or expr[:2] != ["jsonencode", "("]
            or _close(expr, 1) != len(expr) - 1
        ):
            continue
        for container in _object_list(expr[2:-1]):
            yield from _container_environment(container)


def _lambda(tokens: list[str]) -> Iterator[Claim]:
    for env in _groups(tokens, "environment"):
        for key, expr in _fields(env):
            if key == "variables" and expr[:1] == ["{"] and _close(expr, 0) == len(expr) - 1:
                yield from _env_claims(_literals(expr[1:-1]).items())


def _kubernetes(tokens: list[str]) -> Iterator[Claim]:
    for spec in _groups(tokens, "spec"):
        if _literals(spec).get("type") == "ExternalName":
            return
    aliases: list[str] = []
    for spec in _groups(tokens, "spec"):
        for key, expr in _fields(spec):
            if key == "selector" and expr[:1] == ["{"]:
                labels = _literals(expr[1:-1])
                aliases.extend(
                    labels[k].lower() for k in ("app", "app.kubernetes.io/name") if k in labels
                )
    for metadata in _groups(tokens, "metadata"):
        name = _literals(metadata).get("name")
        if name:
            yield Claim(name=name.lower(), aliases=tuple(aliases), own_repo=True)


def _resource(kind: str, tokens: list[str]) -> Iterator[Claim]:
    if kind == "aws_ecs_task_definition":
        yield from _ecs(tokens)
    elif kind == "aws_lambda_function":
        yield from _lambda(tokens)
    elif kind.startswith("google_cloud_run"):
        for env in _groups(tokens, "env"):
            yield from _named_envs(env)
    elif kind.startswith("kubernetes_service"):
        yield from _kubernetes(tokens)


def read(file: SourceFile) -> Iterable[Claim]:
    tokens = _tokens(file.text)
    i = 0
    while i + 3 < len(tokens):
        if tokens[i] == "resource" and tokens[i + 3] == "{":
            kind = _literal(tokens[i + 1])
            end = _close(tokens, i + 3)
            if end < 0:
                return
            if kind in _TYPES:
                yield from _resource(kind, tokens[i + 4 : end])
            i = end + 1
        elif tokens[i] in _PAIRS:
            end = _close(tokens, i)
            if end < 0:
                return
            i = end + 1
        else:
            i += 1


SOURCE = ServiceNameSource(name="terraform", filenames=("*.tf",), marker="resource", read=read)

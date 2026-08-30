"""Generate the TypeScript HTTP contract from the live FastAPI schema.

The wire types under ``packages/types/src`` are hand-written and were drifting
from the Pydantic models that actually serialize: a field added on one side
went missing on the other silently, because both sides degrade rather than
throw. This emits the HTTP half from ``/openapi.json`` so that half cannot
drift, and leaves the hand-written artifact/UI/domain types alone.

Run ``python scripts/generate_http_types.py`` to refresh, ``--check`` to fail
when the checked-in file is stale.

Determinism, since CI diffs the output: schema names are sorted, property order
follows the model's declaration order (Pydantic preserves it), and ``info`` is
never read — it carries the release version, which would otherwise register as
drift on every version bump.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Any

_OUT = pathlib.Path(__file__).resolve().parents[1] / "packages/types/src/generated/http.ts"

_PRIMITIVES = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}

_HEADER = """// Generated from the FastAPI application's OpenAPI schema. Do not edit.
//
// Regenerate with:  python scripts/generate_http_types.py
// CI fails when this file and the live schema disagree.
//
// Scope: the HTTP boundary only. Artifact, UI and other non-wire domain types
// stay hand-written in the sibling modules.
//
// `?` mirrors the schema's `required` list, which states what a request may
// omit. A response field with a server-side default is still always sent.
"""


def _identifier(name: str) -> str:
    """A schema name as a TypeScript identifier."""
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", name)
    return f"_{cleaned}" if cleaned[:1].isdigit() else cleaned


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _type_of(schema: Any) -> str:
    """One OpenAPI schema node as a TypeScript type expression."""
    if not isinstance(schema, dict) or not schema:
        return "unknown"

    ref = schema.get("$ref")
    if ref:
        return _identifier(ref.rsplit("/", 1)[-1])

    if "const" in schema:
        return _literal(schema["const"])

    if "enum" in schema:
        members = [_literal(v) for v in schema["enum"]]
        return " | ".join(members) if members else "never"

    for combinator, joiner in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        if combinator in schema:
            parts = [_type_of(s) for s in schema[combinator]]
            # A one-armed union is the arm; ``string | null`` must keep both.
            unique = list(dict.fromkeys(parts))
            if not unique:
                return "unknown"
            if len(unique) == 1:
                return unique[0]
            if joiner == " & ":
                # ``A | B & C`` binds as ``A | (B & C)``, so a union arm inside
                # an intersection has to be parenthesized.
                unique = [f"({part})" if "|" in part else part for part in unique]
            return joiner.join(unique)

    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(dict.fromkeys(_PRIMITIVES.get(k, "unknown") for k in kind))

    if kind == "array":
        prefix = schema.get("prefixItems")
        if prefix:
            return "[" + ", ".join(_type_of(entry) for entry in prefix) + "]"
        item = _type_of(schema.get("items", {}))
        # Only a union needs the parentheses; a generic does not.
        return f"({item})[]" if "|" in item or "&" in item else f"{item}[]"

    if kind == "object" or "properties" in schema:
        if "properties" in schema:
            return _object_literal(schema)
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict) and extra:
            return f"Record<string, {_type_of(extra)}>"
        if extra is False:
            return "Record<string, never>"
        return "Record<string, unknown>"

    return _PRIMITIVES.get(kind, "unknown")


def _object_literal(schema: dict[str, Any]) -> str:
    """An inline object schema, for properties that were not given a name."""
    required = set(schema.get("required") or ())
    fields = [
        f"{_property_key(name)}{'' if name in required else '?'}: {_type_of(spec)}"
        for name, spec in (schema.get("properties") or {}).items()
    ]
    return "{ " + "; ".join(fields) + " }" if fields else "Record<string, unknown>"


def _property_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_$][\w$]*", name) else _literal(name)


def _docstring(schema: dict[str, Any]) -> list[str]:
    text = (schema.get("description") or "").strip()
    if not text:
        return []
    lines = [line.rstrip() for line in text.splitlines()]
    if len(lines) == 1:
        return [f"/** {lines[0]} */"]
    return ["/**", *(f" * {line}".rstrip() for line in lines), " */"]


def _declaration(name: str, schema: dict[str, Any]) -> str:
    ident = _identifier(name)
    out: list[str] = _docstring(schema)

    if "properties" not in schema:
        # An alias: a union, an enum, an array or a bare record under a name.
        out.append(f"export type {ident} = {_type_of(schema)};")
        return "\n".join(out)

    required = set(schema.get("required") or ())
    out.append(f"export interface {ident} {{")
    for prop, spec in (schema.get("properties") or {}).items():
        for line in _docstring(spec if isinstance(spec, dict) else {}):
            out.append(f"  {line}")
        optional = "" if prop in required else "?"
        out.append(f"  {_property_key(prop)}{optional}: {_type_of(spec)};")
    out.append("}")
    return "\n".join(out)


def render(openapi: dict[str, Any]) -> str:
    schemas = (openapi.get("components") or {}).get("schemas") or {}
    seen: dict[str, str] = {}
    for name in sorted(schemas):
        ident = _identifier(name)
        first = seen.setdefault(ident, name)
        if first != name:
            raise ValueError(f"{name!r} and {first!r} both emit the identifier {ident!r}")
    blocks = [_declaration(name, schemas[name]) for name in sorted(schemas)]
    return _HEADER + "\n" + "\n\n".join(blocks) + "\n"


def _live_schema() -> dict[str, Any]:
    from repowise.server.app import create_app

    return create_app().openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the file is stale.")
    args = parser.parse_args(argv)

    rendered = render(_live_schema())

    if args.check:
        current = _OUT.read_text(encoding="utf-8") if _OUT.exists() else ""
        if current != rendered:
            print(
                f"{_OUT.relative_to(_OUT.parents[4])} is stale.\n"
                "Run: python scripts/generate_http_types.py",
                file=sys.stderr,
            )
            return 1
        return 0

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the file is committed with an eol=lf attribute, so writing
    # CRLF on Windows would make every local run look like drift.
    with _OUT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

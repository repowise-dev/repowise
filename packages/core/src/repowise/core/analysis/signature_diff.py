"""Semantic signature difference classification for contract impact analysis.

Compares a base signature with a head signature to classify changes as:
- ``none``: Contract identical (formatting, whitespace, trailing commas, receiver shifts).
- ``compatible``: Old callers continue to work (e.g., appended optional parameters with default values).
- ``breaking``: Old callers are broken (removed parameter, added required parameter, reordered arguments).
- ``unknown``: Unparseable or unstructured signature (degrades safely to breaking).
"""

from __future__ import annotations

from dataclasses import dataclass
import re

EFFECT_NONE = "none"
EFFECT_COMPATIBLE = "compatible"
EFFECT_BREAKING = "breaking"
EFFECT_UNKNOWN = "unknown"

_RECEIVER_NAMES = frozenset({"self", "cls", "this"})


@dataclass(frozen=True)
class ParsedParam:
    """Normalized parameter representation."""

    name: str
    default: str | None = None
    type_annotation: str | None = None
    is_vararg: bool = False
    is_kwarg: bool = False
    is_optional: bool = False
    is_receiver: bool = False


def _split_top_level_commas(text: str) -> list[str]:
    """Split parameter list by commas that are not nested in parens/brackets/angles/quotes."""
    items: list[str] = []
    current: list[str] = []
    depth_paren = 0
    depth_bracket = 0
    depth_brace = 0
    depth_angle = 0
    in_single_quote = False
    in_double_quote = False
    escape = False

    for ch in text:
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == "\\":
            current.append(ch)
            escape = True
            continue
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(ch)
            continue
        if ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(ch)
            continue
        if in_single_quote or in_double_quote:
            current.append(ch)
            continue

        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket = max(0, depth_bracket - 1)
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch == "<":
            depth_angle += 1
        elif ch == ">":
            depth_angle = max(0, depth_angle - 1)
        elif (
            ch == ","
            and depth_paren == 0
            and depth_bracket == 0
            and depth_brace == 0
            and depth_angle == 0
        ):
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue

        current.append(ch)

    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _extract_params_and_return(sig: str) -> tuple[str, str | None] | None:
    """Extract (params_inner_string, return_type_string) from signature."""
    sig = sig.strip()
    # Find the outermost parameter parentheses (first '(' to matching ')')
    start = sig.find("(")
    if start == -1:
        return None

    depth = 0
    end = -1
    for idx in range(start, len(sig)):
        ch = sig[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = idx
                break

    if end == -1:
        return None

    params_str = sig[start + 1 : end].strip()
    after = sig[end + 1 :].strip()
    return_type = None
    if "->" in after:
        return_type = after.split("->", 1)[1].strip()
    elif after.startswith(":"):
        return_type = after[1:].strip()

    return params_str, return_type


def _parse_single_param(raw: str, index: int = 0) -> ParsedParam:
    """Parse a single parameter raw string into ParsedParam."""
    p = raw.strip()
    # Check for keyword-only marker in Python bare `*` or positional-only `/`
    if p in ("*", "/"):
        return ParsedParam(name=p, is_optional=True)

    # Check for vararg / kwarg / rest
    is_vararg = False
    is_kwarg = False
    if p.startswith("**"):
        is_kwarg = True
        p = p[2:].strip()
    elif p.startswith("*") or p.startswith("..."):
        is_vararg = True
        p = p.lstrip(".*").strip()

    # Split on default '=' if present
    default_val: str | None = None
    if "=" in p:
        # Split on first top-level '='
        parts = p.split("=", 1)
        p = parts[0].strip()
        default_val = parts[1].strip()

    # Split on ':' for type annotation (Python, TS, Rust, Go)
    type_ann: str | None = None
    if ":" in p:
        parts = p.split(":", 1)
        p = parts[0].strip()
        type_ann = parts[1].strip()

    # Check for TS optional marker 'name?'
    is_optional = default_val is not None or is_vararg or is_kwarg
    if p.endswith("?"):
        p = p[:-1].strip()
        is_optional = True

    # In Go or Java/C/C++, type might be separate token:
    # "int x" (Java/C) -> tokens ["int", "x"] -> name is "x", type is "int"
    # "x int" (Go) -> tokens ["x", "int"] -> name is "x", type is "int"
    tokens = p.split()
    if len(tokens) == 2 and not type_ann:
        c_types = {"int", "long", "float", "double", "char", "void", "bool", "boolean", "string", "String", "Object"}
        if tokens[0] in c_types or tokens[0].endswith("*"):
            type_ann = tokens[0]
            name = tokens[1]
        else:
            name = tokens[0]
            type_ann = tokens[1]
    elif len(tokens) == 1:
        name = tokens[0]
    else:
        name = p

    # Remove any pointer/reference prefixes/suffixes from name
    name = name.strip("&* ")

    is_receiver = (index == 0 and name in _RECEIVER_NAMES)

    return ParsedParam(
        name=name,
        default=default_val,
        type_annotation=type_ann,
        is_vararg=is_vararg,
        is_kwarg=is_kwarg,
        is_optional=is_optional,
        is_receiver=is_receiver,
    )


def parse_parameters(sig_str: str) -> list[ParsedParam] | None:
    """Parse parameter list of a signature string."""
    extracted = _extract_params_and_return(sig_str)
    if extracted is None:
        return None
    params_str, _ = extracted
    if not params_str:
        return []

    raw_items = _split_top_level_commas(params_str)
    return [_parse_single_param(item, idx) for idx, item in enumerate(raw_items)]


def _normalize_sig(sig: str) -> str:
    """Normalize whitespace and formatting."""
    # Collapse multiple spaces and trim
    s = re.sub(r"\s+", " ", sig.strip())
    # Remove trailing commas before closing paren
    s = re.sub(r",\s*\)", ")", s)
    # Remove spaces around parens/colons/equals/arrows
    s = re.sub(r"\s*([(),:=]|->)\s*", r"\1", s)
    return s


def classify_signature_change(
    base_sig: str, head_sig: str, kind: str = "function"
) -> tuple[str, str | None]:
    """Classify semantic change between base_sig and head_sig.

    Returns:
        (signature_effect, signature_reason)
        signature_effect: "none" | "compatible" | "breaking" | "unknown"
    """
    if not base_sig and not head_sig:
        return EFFECT_NONE, None
    if not base_sig or not head_sig:
        return EFFECT_BREAKING, "signature added or removed"

    # Quick exact or normalized match
    if base_sig == head_sig or _normalize_sig(base_sig) == _normalize_sig(head_sig):
        return EFFECT_NONE, None

    base_params = parse_parameters(base_sig)
    head_params = parse_parameters(head_sig)

    if base_params is None or head_params is None:
        return EFFECT_UNKNOWN, "unparseable signature format"

    # Filter out receivers (self/cls) if present on method
    def _strip_receiver(params: list[ParsedParam]) -> list[ParsedParam]:
        if params and params[0].is_receiver:
            return params[1:]
        return params

    b_params = _strip_receiver(base_params)
    h_params = _strip_receiver(head_params)

    # Check if identical after stripping receiver / normalizing defaults
    if b_params == h_params:
        return EFFECT_NONE, None

    b_names = [p.name for p in b_params]
    h_names = [p.name for p in h_params]

    b_map = {p.name: p for p in b_params}
    h_map = {p.name: p for p in h_params}

    # 1. Check for removed parameters
    removed = [name for name in b_names if name not in h_map]
    if removed:
        if len(removed) == 1:
            return EFFECT_BREAKING, f"removed parameter '{removed[0]}'"
        return EFFECT_BREAKING, f"removed parameters {', '.join(repr(r) for r in removed)}"

    # 2. Check for added parameters
    added = [p for p in h_params if p.name not in b_map]
    if added:
        required_added = [p for p in added if not p.is_optional]
        if required_added:
            if len(required_added) == 1:
                return EFFECT_BREAKING, f"added required parameter '{required_added[0].name}'"
            return EFFECT_BREAKING, f"added required parameters {', '.join(repr(p.name) for p in required_added)}"

        # Check where optional params were added. If appended at the end and order of existing params preserved:
        common_indices = [h_names.index(name) for name in b_names]
        if common_indices == sorted(common_indices) and common_indices == list(range(len(b_names))):
            opt_names = [p.name for p in added]
            if len(opt_names) == 1:
                return EFFECT_COMPATIBLE, f"added optional parameter '{opt_names[0]}'"
            return EFFECT_COMPATIBLE, f"added optional parameters {', '.join(repr(o) for o in opt_names)}"
        else:
            return EFFECT_BREAKING, "added parameter disrupts positional order"

    # 3. Check for existing parameters whose default was removed (now required)
    for p_name in b_names:
        bp = b_map[p_name]
        hp = h_map[p_name]
        if bp.is_optional and not hp.is_optional:
            return EFFECT_BREAKING, f"parameter '{p_name}' is now required"

    # 4. Check for reordering
    if b_names != h_names:
        return EFFECT_BREAKING, "reordered parameters"

    # 5. Type changes or subtle annotations
    diff_types = [p_name for p_name in b_names if b_map[p_name].type_annotation != h_map[p_name].type_annotation]
    if diff_types:
        return EFFECT_COMPATIBLE, f"updated parameter type for '{diff_types[0]}'"

    return EFFECT_NONE, None

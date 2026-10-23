"""Lightning Web Components: bundle names, module specifiers and template tags.

An LWC bundle is ``.../lwc/<name>/`` with main module ``<name>.js`` (or ``.ts``).
JS imports reach it as ``c/<name>`` (or the sfdx namespace), templates as
``<c-kebab-name>``, looked up in a reverse index of ``kebab(name)`` because
camelCase cannot be recovered from a tag. ``@salesforce/apex/[ns.]Cls.method``
resolves to the Apex class file. A name two bundles declare goes to the one
sharing the longest directory prefix with the referrer; a tie resolves nothing.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from ...test_paths import is_test_related_path

if TYPE_CHECKING:
    from .context import ResolverContext

APEX_MODULE_PREFIXES = ("@salesforce/apex/", "@salesforce/apexContinuation/")
_SPECIFIER_RE = re.compile(r"([A-Za-z]\w*)/([A-Za-z]\w*)")
_UPPER_RE = re.compile(r"[A-Z]")


def kebab(name: str) -> str:
    """The template tag body for bundle *name*: ``fooBar2Baz`` -> ``foo-bar2-baz``."""
    return _UPPER_RE.sub(lambda m: "-" + m.group().lower(), name)


def lwc_bundle_dir(path: str) -> str | None:
    """``.../lwc/<name>`` for any file inside a bundle (tests included), else None."""
    parts = path.split("/")
    for i in range(len(parts) - 2, 0, -1):
        if parts[i - 1] == "lwc":
            return "/".join(parts[: i + 1])
    return None


def lwc_bundles(ctx: ResolverContext) -> dict[str, tuple[str, ...]]:
    """Bundle name -> main module paths, built once per context."""
    cached = getattr(ctx, "_lwc_bundles", None)
    if cached is not None:
        return cached
    found: dict[str, list[str]] = {}
    for path in ctx.sorted_paths:
        parts = path.split("/")
        if len(parts) < 3 or parts[-3] != "lwc":
            continue
        name = parts[-2]
        if parts[-1] in (f"{name}.js", f"{name}.ts"):
            found.setdefault(name, []).append(path)
    bundles = {name: tuple(paths) for name, paths in found.items()}
    ctx._lwc_bundles = bundles  # type: ignore[attr-defined]
    return bundles


def lwc_tag_index(ctx: ResolverContext) -> dict[str, str]:
    """``kebab(name)`` -> bundle name. Distinct legal names never share a kebab form."""
    cached = getattr(ctx, "_lwc_tag_index", None)
    if cached is None:
        cached = {kebab(name): name for name in lwc_bundles(ctx)}
        ctx._lwc_tag_index = cached  # type: ignore[attr-defined]
    return cached


def lwc_namespaces(ctx: ResolverContext) -> frozenset[str]:
    """``c`` plus every sfdx ``namespace`` the repository declares for itself, lowercased."""
    cached = getattr(ctx, "_lwc_namespaces", None)
    if cached is not None:
        return cached
    names = {"c"}
    if ctx.repo_path is not None:
        manifests = {p for p in ctx.path_set if p.rpartition("/")[2] == "sfdx-project.json"}
        manifests.add("sfdx-project.json")
        for rel in sorted(manifests):
            try:
                text = (ctx.repo_path / rel).read_text(encoding="utf-8-sig")
                namespace = json.loads(text).get("namespace")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(namespace, str) and namespace.strip():
                names.add(namespace.strip().lower())
    result = frozenset(names)
    ctx._lwc_namespaces = result  # type: ignore[attr-defined]
    return result


def nearest(candidates: tuple[str, ...], referrer: str) -> str | None:
    """The candidate sharing the most leading directories with *referrer*; None on a tie."""
    if len(candidates) == 1:
        return candidates[0]
    ref = referrer.split("/")

    def shared(path: str) -> int:
        n = 0
        for a, b in zip(path.split("/"), ref, strict=False):
            if a != b:
                break
            n += 1
        return n

    scored = sorted(((shared(c), c) for c in candidates), reverse=True)
    if scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _apex_classes(ctx: ResolverContext) -> dict[str, tuple[str, ...]]:
    """Lowercased Apex class name -> ``.cls`` paths (Apex names are case-insensitive).

    Ceiling: a local index; use a shared case-insensitive Apex index once one exists.
    """
    cached = getattr(ctx, "_lwc_apex_classes", None)
    if cached is None:
        found: dict[str, list[str]] = {}
        for path in ctx.sorted_paths:
            if path[-4:].lower() == ".cls":
                stem = path.rpartition("/")[2][:-4].lower()
                found.setdefault(stem, []).append(path)
        cached = {name: tuple(paths) for name, paths in found.items()}
        ctx._lwc_apex_classes = cached  # type: ignore[attr-defined]
    return cached


def _resolve_apex_module(spec: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """``[ns.]Cls.method`` -> the class file; another package's namespace stays external."""
    parts = spec.split(".")
    if len(parts) == 3:
        if parts[0].lower() not in lwc_namespaces(ctx):
            return None
        parts = parts[1:]
    if len(parts) != 2 or not all(parts):
        return None
    candidates = _apex_classes(ctx).get(parts[0].lower())
    return nearest(candidates, importer_path) if candidates else None


def resolve_lwc_module(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """A module inside an LWC bundle: ``c/fooBar`` -> that bundle's main module,
    ``@salesforce/apex/Cls.method`` -> the Apex class file. Other Salesforce
    modules (schema, labels, ``lightning/*``) are not files here and stay external.
    """
    for prefix in APEX_MODULE_PREFIXES:
        if module_path.startswith(prefix):
            # Jest only mocks Apex, so a test's import is no use of the class.
            if is_test_related_path(importer_path, "javascript"):
                return None
            return _resolve_apex_module(module_path[len(prefix) :], importer_path, ctx)
    m = _SPECIFIER_RE.fullmatch(module_path)
    if m is None or m.group(1).lower() not in lwc_namespaces(ctx):
        return None
    candidates = lwc_bundles(ctx).get(m.group(2))
    return nearest(candidates, importer_path) if candidates else None

"""Bazel BUILD reader — narrow ``cc_*`` rule extraction.

Like :mod:`.cmake`, serves two roles:

1. Build-graph extraction (``cc_binary`` / ``cc_library`` / ``cc_test`` /
   ``cc_proto_library`` / ``cc_grpc_library``) consumed by
   :mod:`repowise.core.ingestion.resolvers.cpp_workspace`.

2. ``ManifestParser`` shape — emits no external records today (Bazel
   external deps live in ``WORKSPACE`` / ``MODULE.bazel`` and require
   their own grammar). Registered so the manifest discovery walk picks
   up BUILD files cheaply.

The reader recognises Starlark function-call literals at the top level —
``cc_binary(name = "x", srcs = ["a.cc"], hdrs = ["a.h"], deps = [":b"])``.
List values, kwarg syntax, and string literals are tokenised with a
small dedicated scanner. Anything more complex (Starlark loops, list
comprehensions, ``select(...)``, ``glob(...)``) falls back to "list of
literal strings collected from the call".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import structlog

from .base import ExternalSystemRecord

log = structlog.get_logger(__name__)

filenames: tuple[str, ...] = ("BUILD", "BUILD.bazel")
ecosystem: str = "bazel"


@dataclass
class BazelTarget:
    name: str
    kind: str  # cc_binary | cc_library | cc_test | cc_proto_library | cc_grpc_library | cc_fuzz_test
    build_file: str  # repo-relative POSIX path
    package: str  # repo-relative dir owning the BUILD
    srcs: list[str] = field(default_factory=list)
    hdrs: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    testonly: bool = False


@dataclass
class BazelFile:
    path: str  # repo-relative POSIX path to BUILD/BUILD.bazel
    package: str  # repo-relative dir
    targets: list[BazelTarget] = field(default_factory=list)


_CC_RULE_NAMES: frozenset[str] = frozenset({
    "cc_binary",
    "cc_library",
    "cc_test",
    "cc_proto_library",
    "cc_grpc_library",
    "cc_fuzz_test",
    "cc_shared_library",
})

# Matches ``rule_name(`` at start of line (or after whitespace) — enough
# for top-level rule discovery. Nested rule calls inside macros are
# missed; that's acceptable for the first pass.
_RULE_CALL_RE = re.compile(
    r"(?:^|\n)\s*(" + "|".join(re.escape(r) for r in _CC_RULE_NAMES) + r")\s*\(",
    re.MULTILINE,
)

_STRING_LIT_RE = re.compile(r'"((?:\\.|[^"\\])*)"')


def _slice_call_body(text: str, open_paren_pos: int) -> str:
    """Return the substring between the matching ``(`` and ``)`` (exclusive)."""
    depth = 1
    i = open_paren_pos + 1
    n = len(text)
    while i < n and depth > 0:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        if ch == "'":
            i += 1
            while i < n and text[i] != "'":
                if text[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        if ch == "(" or ch == "[" or ch == "{":
            depth += 1
        elif ch == ")" or ch == "]" or ch == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[open_paren_pos + 1 : i]


_KWARG_RE = re.compile(
    r"\s*,?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*",
)


def _extract_kwargs(call_body: str) -> dict[str, str]:
    """Split a call body into ``kwarg → raw RHS``.

    The RHS is everything up to the next top-level comma (paren-aware).
    No further parsing is done here — list / boolean recognition is done
    by the callers via ``_collect_strings`` / ``_truthy``.
    """
    out: dict[str, str] = {}
    i = 0
    n = len(call_body)
    while i < n:
        m = _KWARG_RE.search(call_body, i)
        if not m:
            break
        key = m.group(1)
        rhs_start = m.end()
        depth = 0
        j = rhs_start
        while j < n:
            ch = call_body[j]
            if ch == '"':
                j += 1
                while j < n and call_body[j] != '"':
                    if call_body[j] == "\\" and j + 1 < n:
                        j += 2
                        continue
                    j += 1
                j += 1
                continue
            if ch == "'":
                j += 1
                while j < n and call_body[j] != "'":
                    if call_body[j] == "\\" and j + 1 < n:
                        j += 2
                        continue
                    j += 1
                j += 1
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                break
            j += 1
        out[key] = call_body[rhs_start:j].strip()
        i = j + 1
    return out


def _collect_strings(rhs: str) -> list[str]:
    """Return every string literal in *rhs* in source order."""
    return [m.group(1) for m in _STRING_LIT_RE.finditer(rhs)]


def _truthy(rhs: str) -> bool:
    s = rhs.strip().rstrip(",").strip()
    return s.lower() in ("true", "1")


def parse_bazel_build(
    build_path: Path,
    *,
    repo_root: Path | None = None,
) -> BazelFile:
    """Parse one ``BUILD`` / ``BUILD.bazel`` file into a :class:`BazelFile`."""
    try:
        text = build_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""

    if repo_root is not None:
        try:
            rel_self = build_path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel_self = build_path.as_posix()
    else:
        rel_self = build_path.as_posix()
    package = PurePosixPath(rel_self).parent.as_posix() if "/" in rel_self else ""
    if package == ".":
        package = ""

    bf = BazelFile(path=rel_self, package=package)

    for match in _RULE_CALL_RE.finditer(text):
        kind = match.group(1)
        open_paren = match.end() - 1
        body = _slice_call_body(text, open_paren)
        kwargs = _extract_kwargs(body)

        name = ""
        for s in _collect_strings(kwargs.get("name", "")):
            name = s
            break
        if not name:
            continue

        srcs = _collect_strings(kwargs.get("srcs", ""))
        hdrs = _collect_strings(kwargs.get("hdrs", ""))
        deps = _collect_strings(kwargs.get("deps", ""))
        includes = _collect_strings(kwargs.get("includes", ""))
        textual_hdrs = _collect_strings(kwargs.get("textual_hdrs", ""))
        testonly = _truthy(kwargs.get("testonly", "False"))

        hdrs.extend(textual_hdrs)

        # Resolve each src/hdr to a repo-relative path. Bazel labels in
        # ``srcs``/``hdrs`` are usually plain filenames relative to the
        # package; ``//pkg:foo.cc`` and ``:foo.cc`` forms are also valid.
        resolved_srcs = [_resolve_label(s, package) for s in srcs]
        resolved_srcs = [s for s in resolved_srcs if s]
        resolved_hdrs = [_resolve_label(s, package) for s in hdrs]
        resolved_hdrs = [s for s in resolved_hdrs if s]

        bf.targets.append(
            BazelTarget(
                name=name,
                kind=kind,
                build_file=rel_self,
                package=package,
                srcs=resolved_srcs,
                hdrs=resolved_hdrs,
                deps=deps,
                includes=includes,
                testonly=testonly or kind == "cc_test" or kind == "cc_fuzz_test",
            )
        )

    return bf


def _resolve_label(label: str, package: str) -> str:
    """Turn a Bazel label / filename into a repo-relative POSIX path.

    Handles:
      * Plain filenames (``foo.cc``) → joined with the package dir.
      * Local labels (``:foo.cc``) → joined with the package dir.
      * Absolute labels (``//pkg/sub:foo.cc``) → ``pkg/sub/foo.cc``.
      * External (``@repo//pkg:foo.cc``) → discarded.
    """
    if not label or label.startswith("@"):
        return ""
    if label.startswith("//"):
        rest = label[2:]
        if ":" in rest:
            pkg, _, fname = rest.partition(":")
            return f"{pkg}/{fname}" if pkg else fname
        return rest
    if label.startswith(":"):
        label = label[1:]
    if package:
        return f"{package}/{label}"
    return label


def discover_bazel_packages(repo_root: Path, *, max_files: int = 5000) -> list[BazelFile]:
    """Walk the repo for every ``BUILD`` / ``BUILD.bazel`` file."""
    repo_root = repo_root.resolve()
    out: list[BazelFile] = []
    skip_dirs = {".git", "node_modules", "bazel-bin", "bazel-out", "bazel-testlogs",
                 "bazel-" + repo_root.name, ".venv", "venv", "build"}
    from repowise.core.fs_walk import iter_glob

    for candidate in ("BUILD", "BUILD.bazel"):
        for p in iter_glob(repo_root, candidate):
            try:
                rel = p.resolve().relative_to(repo_root).as_posix()
            except ValueError:
                continue
            parts = PurePosixPath(rel).parts
            if any(part in skip_dirs for part in parts):
                continue
            if len(out) >= max_files:
                break
            out.append(parse_bazel_build(p, repo_root=repo_root))
    return out


def is_bazel_repo(repo_root: Path) -> bool:
    for marker in ("WORKSPACE", "WORKSPACE.bazel", "MODULE.bazel"):
        if (repo_root / marker).exists():
            return True
    return False


# ---------------------------------------------------------------------------
# ManifestParser shape — empty list (Bazel external deps live elsewhere)
# ---------------------------------------------------------------------------


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    return []


__all__ = [
    "BazelTarget",
    "BazelFile",
    "parse_bazel_build",
    "discover_bazel_packages",
    "is_bazel_repo",
    "parse",
    "filenames",
    "ecosystem",
]

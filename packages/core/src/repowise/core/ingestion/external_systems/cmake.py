"""CMake build-system reader.

Serves two purposes:

1. **Build-graph extraction** for the C/C++ workspace index — ``add_executable``,
   ``add_library``, ``target_sources``, ``target_include_directories``,
   ``target_compile_definitions``, ``add_subdirectory``, ``option``, ``if/endif``
   conditional source membership. Consumed by
   :mod:`repowise.core.ingestion.resolvers.cpp_workspace`.

2. **External dependency extraction** (``ManifestParser`` shape) — ``find_package``
   calls land as ``cmake`` ecosystem records so the manifest classifier can list
   them alongside ``package.json`` / ``Cargo.toml`` deps.

The reader is **regex-tokenised**, not a full CMake evaluator. Best-effort
variable expansion supports ``${VAR}`` against literal ``set(VAR …)``
definitions seen earlier in the same file. Unknown variables are left as-is
and any downstream consumer falls back to stem matching.

If a ``build/.cmake/api/v1/reply/`` directory exists, callers should prefer
:func:`parse_cmake_file_api_reply` — it gives the authoritative target graph.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import structlog

from .base import ExternalSystemRecord

log = structlog.get_logger(__name__)

filenames: tuple[str, ...] = ("CMakeLists.txt",)
ecosystem: str = "cmake"


# ---------------------------------------------------------------------------
# Build-graph types
# ---------------------------------------------------------------------------


@dataclass
class CMakeTarget:
    """A single CMake target (``add_executable`` / ``add_library`` / test)."""

    name: str
    kind: str  # executable | library_static | library_shared | library_interface | library_object | library_module | test | unknown
    cmakelists: str  # repo-relative POSIX path to defining CMakeLists.txt
    sources: list[str] = field(default_factory=list)
    public_headers: list[str] = field(default_factory=list)
    private_headers: list[str] = field(default_factory=list)
    include_dirs: list[str] = field(default_factory=list)  # PUBLIC + INTERFACE
    private_include_dirs: list[str] = field(default_factory=list)
    compile_defines: list[str] = field(default_factory=list)
    link_deps: list[str] = field(default_factory=list)
    conditional_sources: list[str] = field(default_factory=list)
    """Sources added inside an ``if(...)`` block — used by dead-code
    analysis to pair mutually-exclusive alternatives (e.g.,
    ``env_posix.cc`` vs ``env_windows.cc``)."""


@dataclass
class CMakeFile:
    """Parsed CMakeLists.txt — targets, subdirectories, variables."""

    path: str  # repo-relative POSIX path to CMakeLists.txt
    targets: list[CMakeTarget] = field(default_factory=list)
    subdirectories: list[str] = field(default_factory=list)
    """Repo-relative subdirectories from ``add_subdirectory(...)``."""

    variables: dict[str, str] = field(default_factory=dict)
    """Best-effort ``set(VAR literal)`` map."""

    options: list[str] = field(default_factory=list)
    """``option(NAME …)`` flags (used to detect conditional blocks)."""

    find_packages: list[tuple[str, str | None]] = field(default_factory=list)
    """``find_package(NAME [VERSION])`` calls."""


# ---------------------------------------------------------------------------
# Tokenising / command extraction
# ---------------------------------------------------------------------------

# Strip CMake comments (``# ...`` to end-of-line, NOT inside strings) and
# bracket comments ``#[[ ... ]]``. The bracket form is rare; handle the
# simple ``#[[…]]`` pattern only.
_BRACKET_COMMENT_RE = re.compile(r"#\[\[.*?]]", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"#[^\n]*")

# Command extractor: NAME ( ARGS )  — CMake commands are case-insensitive.
_COMMAND_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)

# Argument splitter — splits on whitespace but keeps double-quoted strings
# (with backslash escapes) intact.
_ARG_RE = re.compile(r'"((?:\\.|[^"\\])*)"|(\S+)')

# ``set(VAR <literal>)`` capture — only the literal-RHS form. Anything with
# ``CACHE``, generator expressions, or list expansion falls back to whatever
# the variable map already had (typically nothing).
_SET_RHS_TERMINATORS: frozenset[str] = frozenset({
    "CACHE", "PARENT_SCOPE", "FORCE",
})

_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_HEADER_EXTS: tuple[str, ...] = (".h", ".hpp", ".hxx", ".hh", ".h++", ".inc")
_SOURCE_EXTS: tuple[str, ...] = (".c", ".cc", ".cpp", ".cxx", ".c++", ".cppm", ".ixx", ".mxx")


def _strip_comments(text: str) -> str:
    text = _BRACKET_COMMENT_RE.sub("", text)
    return _LINE_COMMENT_RE.sub("", text)


def _split_args(args_text: str) -> list[str]:
    """Split a CMake command's argument string into individual tokens."""
    out: list[str] = []
    for match in _ARG_RE.finditer(args_text):
        quoted, bare = match.groups()
        if quoted is not None:
            out.append(quoted)
        elif bare:
            out.append(bare)
    return out


def _expand_vars(token: str, variables: dict[str, str]) -> str:
    """Expand ``${VAR}`` references in *token* using *variables*."""
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        return variables.get(name, m.group(0))

    # Up to 3 expansion passes for nested refs
    prev = token
    for _ in range(3):
        nxt = _VAR_REF_RE.sub(repl, prev)
        if nxt == prev:
            break
        prev = nxt
    return prev


@dataclass
class _Command:
    name: str  # lowercased
    args: list[str]
    in_conditional: bool


def _scan_commands(text: str) -> list[_Command]:
    """Scan stripped CMake text and yield commands with ``if``-depth tracking.

    ``if(...)/endif()`` nesting is approximate — we don't evaluate the
    condition; we just flag any command that appears inside any ``if`` block
    so the caller can mark its source list as conditional.
    """
    cmds: list[_Command] = []
    if_depth = 0
    pos = 0
    n = len(text)
    while pos < n:
        m = _COMMAND_RE.search(text, pos)
        if not m:
            break
        cmd_name = m.group(1).lower()
        # Walk the parenthesised argument list, respecting nested parens
        # (variable references like ``$<...>`` are not parens so safe).
        args_start = m.end()
        depth = 1
        i = args_start
        while i < n and depth > 0:
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            elif ch == '"':
                # Skip past quoted string respecting backslash escapes
                i += 1
                while i < n and text[i] != '"':
                    if text[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    i += 1
            i += 1
        args_text = text[args_start:i]
        pos = i + 1

        args = _split_args(args_text)
        cmds.append(_Command(name=cmd_name, args=args, in_conditional=if_depth > 0))

        if cmd_name == "if":
            if_depth += 1
        elif cmd_name == "endif":
            if_depth = max(0, if_depth - 1)

    return cmds


# ---------------------------------------------------------------------------
# Per-file parse
# ---------------------------------------------------------------------------


def _classify_target_kind(library_type: str) -> str:
    t = library_type.upper()
    if t == "STATIC":
        return "library_static"
    if t == "SHARED" or t == "MODULE":
        return "library_shared"
    if t == "INTERFACE":
        return "library_interface"
    if t == "OBJECT":
        return "library_object"
    return "library_static"  # default per CMake


def _classify_path(rel_posix: str) -> str:
    """Return ``"header"``, ``"source"`` or ``"other"`` from extension."""
    lower = rel_posix.lower()
    for ext in _HEADER_EXTS:
        if lower.endswith(ext):
            return "header"
    for ext in _SOURCE_EXTS:
        if lower.endswith(ext):
            return "source"
    return "other"


def _resolve_repo_rel(
    cmakelists_dir: str,
    raw_path: str,
    *,
    repo_root_posix: str | None = None,
) -> str:
    """Resolve a CMake-relative path to a repo-relative POSIX path.

    *cmakelists_dir* is the dir containing the CMakeLists, repo-relative.
    *raw_path* may be absolute (``${CMAKE_CURRENT_SOURCE_DIR}/x`` expanded),
    relative to the CMakeLists, or contain unexpanded ``${VAR}`` refs (which
    we leave verbatim so the caller can stem-match).
    """
    if not raw_path or "${" in raw_path:
        return raw_path  # unexpanded — let caller stem-match
    if raw_path.startswith("/") and repo_root_posix and raw_path.startswith(repo_root_posix + "/"):
        return raw_path[len(repo_root_posix) + 1:]
    if raw_path.startswith("/"):
        return raw_path  # outside repo — caller will discard
    joined = (PurePosixPath(cmakelists_dir) / raw_path).as_posix() if cmakelists_dir else raw_path
    # Collapse ../ and ./ segments
    parts: list[str] = []
    for seg in joined.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def parse_cmake_lists(
    cmakelists_path: Path,
    *,
    repo_root: Path | None = None,
) -> CMakeFile:
    """Parse one CMakeLists.txt into a :class:`CMakeFile`.

    *cmakelists_path* should be an absolute path on disk; *repo_root* is
    used to derive the repo-relative POSIX path stored in ``CMakeFile.path``.
    """
    try:
        text = cmakelists_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    text = _strip_comments(text)
    commands = _scan_commands(text)

    if repo_root is not None:
        try:
            rel_self = cmakelists_path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel_self = cmakelists_path.as_posix()
    else:
        rel_self = cmakelists_path.as_posix()
    cmakelists_dir = PurePosixPath(rel_self).parent.as_posix() if "/" in rel_self else ""
    if cmakelists_dir == ".":
        cmakelists_dir = ""
    repo_root_posix = repo_root.resolve().as_posix() if repo_root else None

    cm = CMakeFile(path=rel_self)

    # Pre-seed implicit variables so common patterns expand.
    cm.variables["CMAKE_CURRENT_SOURCE_DIR"] = cmakelists_dir or "."
    cm.variables["CMAKE_CURRENT_LIST_DIR"] = cmakelists_dir or "."
    if repo_root_posix:
        cm.variables["PROJECT_SOURCE_DIR"] = repo_root_posix
        cm.variables["CMAKE_SOURCE_DIR"] = repo_root_posix

    for cmd in commands:
        name = cmd.name
        # Expand variables in arguments lazily for commands that need it
        if name == "set" and cmd.args:
            var = cmd.args[0]
            rhs_tokens: list[str] = []
            for tok in cmd.args[1:]:
                if tok.upper() in _SET_RHS_TERMINATORS:
                    break
                rhs_tokens.append(_expand_vars(tok, cm.variables))
            if rhs_tokens:
                cm.variables[var] = " ".join(rhs_tokens) if len(rhs_tokens) > 1 else rhs_tokens[0]

        elif name == "option" and cmd.args:
            cm.options.append(cmd.args[0])

        elif name == "find_package" and cmd.args:
            pkg = cmd.args[0]
            version: str | None = None
            for tok in cmd.args[1:]:
                if re.match(r"^\d", tok):
                    version = tok
                    break
            cm.find_packages.append((pkg, version))

        elif name == "add_subdirectory" and cmd.args:
            sub = _expand_vars(cmd.args[0], cm.variables)
            rel = _resolve_repo_rel(cmakelists_dir, sub, repo_root_posix=repo_root_posix)
            if rel:
                cm.subdirectories.append(rel)

        elif name == "add_executable" and cmd.args:
            target = _parse_add_target(
                cmd, kind="executable", cmakelists=rel_self,
                cmakelists_dir=cmakelists_dir, cm=cm,
                repo_root_posix=repo_root_posix,
            )
            if target:
                cm.targets.append(target)

        elif name == "add_library" and cmd.args:
            # add_library(name [STATIC|SHARED|MODULE|OBJECT|INTERFACE] ...)
            target_name = cmd.args[0]
            kind = "library_static"
            sources_start = 1
            if len(cmd.args) >= 2 and cmd.args[1].upper() in (
                "STATIC", "SHARED", "MODULE", "OBJECT", "INTERFACE", "IMPORTED",
            ):
                kind = _classify_target_kind(cmd.args[1])
                sources_start = 2
            t = CMakeTarget(name=target_name, kind=kind, cmakelists=rel_self)
            _ingest_sources(
                t, cmd.args[sources_start:], cmakelists_dir, cm,
                conditional=cmd.in_conditional, repo_root_posix=repo_root_posix,
            )
            cm.targets.append(t)

        elif name == "target_sources" and cmd.args:
            _apply_target_sources(
                cmd, cm, cmakelists_dir, repo_root_posix=repo_root_posix,
            )

        elif name == "target_include_directories" and cmd.args:
            _apply_target_include_dirs(
                cmd, cm, cmakelists_dir, repo_root_posix=repo_root_posix,
            )

        elif name == "target_compile_definitions" and cmd.args:
            _apply_target_compile_defs(cmd, cm)

        elif name == "target_link_libraries" and cmd.args:
            _apply_target_link_libs(cmd, cm)

        elif name == "add_test" and cmd.args:
            # add_test(NAME tname COMMAND target ...) or
            # add_test(tname target args...)
            # We use this primarily to flag library targets that drive tests;
            # the second positional after NAME/keyword is the executable.
            args = cmd.args
            if args[0].upper() == "NAME" and len(args) >= 4 and args[2].upper() == "COMMAND":
                ref_target = args[3]
            elif len(args) >= 2:
                ref_target = args[1]
            else:
                ref_target = None
            if ref_target:
                for t in cm.targets:
                    if t.name == ref_target and t.kind == "executable":
                        t.kind = "test"

    return cm


def _parse_add_target(
    cmd: _Command,
    *,
    kind: str,
    cmakelists: str,
    cmakelists_dir: str,
    cm: CMakeFile,
    repo_root_posix: str | None,
) -> CMakeTarget | None:
    name = cmd.args[0]
    # ``add_executable(name)`` with no sources is valid — sources arrive via
    # ``target_sources(...)``. Build the target with whatever inline sources
    # there are; ``_apply_target_sources`` fills in the rest.
    t = CMakeTarget(name=name, kind=kind, cmakelists=cmakelists)
    _ingest_sources(
        t, cmd.args[1:], cmakelists_dir, cm,
        conditional=cmd.in_conditional, repo_root_posix=repo_root_posix,
    )
    return t


def _ingest_sources(
    target: CMakeTarget,
    raw_args: Sequence[str],
    cmakelists_dir: str,
    cm: CMakeFile,
    *,
    conditional: bool,
    repo_root_posix: str | None,
) -> None:
    """Append each path-like argument to the target's source/header lists."""
    for raw in raw_args:
        if raw.upper() in ("WIN32", "MACOSX_BUNDLE", "EXCLUDE_FROM_ALL"):
            continue
        expanded = _expand_vars(raw, cm.variables)
        # ``set(SRC a.cc b.cc)`` was stored as a space-joined string; split.
        for tok in expanded.split():
            tok = tok.strip()
            if not tok or tok.startswith("$<"):
                continue
            rel = _resolve_repo_rel(
                cmakelists_dir, tok, repo_root_posix=repo_root_posix,
            )
            if not rel or rel.startswith("/"):
                continue
            cls = _classify_path(rel)
            if cls == "header":
                target.private_headers.append(rel)
            elif cls == "source":
                target.sources.append(rel)
                if conditional:
                    target.conditional_sources.append(rel)


_SCOPE_KEYWORDS: frozenset[str] = frozenset({"PUBLIC", "PRIVATE", "INTERFACE"})


def _apply_target_sources(
    cmd: _Command,
    cm: CMakeFile,
    cmakelists_dir: str,
    *,
    repo_root_posix: str | None,
) -> None:
    args = cmd.args
    if not args:
        return
    target_name = args[0]
    target = next((t for t in cm.targets if t.name == target_name), None)
    # If the target was declared in a parent file, create a stub so the
    # source list survives — the reactor merges these later.
    if target is None:
        target = CMakeTarget(name=target_name, kind="unknown", cmakelists=cm.path)
        cm.targets.append(target)

    scope = "PRIVATE"
    for tok in args[1:]:
        upper = tok.upper()
        if upper in _SCOPE_KEYWORDS or upper in ("FILE_SET", "BASE_DIRS", "FILES", "TYPE"):
            if upper in _SCOPE_KEYWORDS:
                scope = upper
            continue
        expanded = _expand_vars(tok, cm.variables)
        for part in expanded.split():
            part = part.strip()
            if not part or part.startswith("$<"):
                continue
            rel = _resolve_repo_rel(cmakelists_dir, part, repo_root_posix=repo_root_posix)
            if not rel or rel.startswith("/"):
                continue
            cls = _classify_path(rel)
            if cls == "header":
                if scope == "PUBLIC" or scope == "INTERFACE":
                    target.public_headers.append(rel)
                else:
                    target.private_headers.append(rel)
            elif cls == "source":
                target.sources.append(rel)
                if cmd.in_conditional:
                    target.conditional_sources.append(rel)


def _apply_target_include_dirs(
    cmd: _Command,
    cm: CMakeFile,
    cmakelists_dir: str,
    *,
    repo_root_posix: str | None,
) -> None:
    args = cmd.args
    if not args:
        return
    target_name = args[0]
    target = next((t for t in cm.targets if t.name == target_name), None)
    if target is None:
        target = CMakeTarget(name=target_name, kind="unknown", cmakelists=cm.path)
        cm.targets.append(target)

    scope = "PRIVATE"
    for tok in args[1:]:
        upper = tok.upper()
        if upper in _SCOPE_KEYWORDS or upper in ("SYSTEM", "AFTER", "BEFORE"):
            if upper in _SCOPE_KEYWORDS:
                scope = upper
            continue
        expanded = _expand_vars(tok, cm.variables)
        for part in expanded.split():
            part = part.strip()
            if not part or part.startswith("$<"):
                continue
            # Strip generator-expression wrappers like
            # ``$<BUILD_INTERFACE:include>`` — keep just the inner path.
            if ":" in part and part.startswith("$<") and ">" in part:
                inner = part[part.find(":") + 1 : part.rfind(">")]
                part = inner
            rel = _resolve_repo_rel(cmakelists_dir, part, repo_root_posix=repo_root_posix)
            if not rel:
                continue
            if scope in ("PUBLIC", "INTERFACE"):
                target.include_dirs.append(rel)
            else:
                target.private_include_dirs.append(rel)


def _apply_target_compile_defs(cmd: _Command, cm: CMakeFile) -> None:
    args = cmd.args
    if not args:
        return
    target_name = args[0]
    target = next((t for t in cm.targets if t.name == target_name), None)
    if target is None:
        target = CMakeTarget(name=target_name, kind="unknown", cmakelists=cm.path)
        cm.targets.append(target)
    for tok in args[1:]:
        upper = tok.upper()
        if upper in _SCOPE_KEYWORDS:
            continue
        # ``-DFOO=1`` and bare ``FOO`` both land
        if tok.startswith("-D"):
            tok = tok[2:]
        # Strip ``=value`` for the macro-name set
        macro = tok.split("=", 1)[0]
        if macro:
            target.compile_defines.append(macro)


def _apply_target_link_libs(cmd: _Command, cm: CMakeFile) -> None:
    args = cmd.args
    if not args:
        return
    target_name = args[0]
    target = next((t for t in cm.targets if t.name == target_name), None)
    if target is None:
        target = CMakeTarget(name=target_name, kind="unknown", cmakelists=cm.path)
        cm.targets.append(target)
    for tok in args[1:]:
        if tok.upper() in _SCOPE_KEYWORDS:
            continue
        target.link_deps.append(tok)


# ---------------------------------------------------------------------------
# Reactor discovery
# ---------------------------------------------------------------------------


def discover_cmake_reactor(
    repo_root: Path,
    *,
    max_files: int = 2000,
) -> list[CMakeFile]:
    """Walk the repo from ``repo_root`` following ``add_subdirectory`` links.

    Starts at ``<repo_root>/CMakeLists.txt`` (if present) and recurses into
    every subdirectory it references. If the root file is missing, falls
    back to any top-level subdirectory that owns a CMakeLists.txt — this
    catches projects that nest their build under ``src/`` or similar.
    Caps at ``max_files`` to avoid runaway walks on monorepos.
    """
    repo_root = repo_root.resolve()
    out: list[CMakeFile] = []
    seen: set[str] = set()

    def visit(rel_dir: str) -> None:
        if len(out) >= max_files:
            return
        target = (repo_root / rel_dir / "CMakeLists.txt").resolve() if rel_dir else (repo_root / "CMakeLists.txt").resolve()
        try:
            target_rel = target.relative_to(repo_root).as_posix()
        except ValueError:
            return
        if target_rel in seen or not target.exists():
            return
        seen.add(target_rel)
        cm = parse_cmake_lists(target, repo_root=repo_root)
        out.append(cm)
        for sub in cm.subdirectories:
            visit(sub)

    if (repo_root / "CMakeLists.txt").exists():
        visit("")
    else:
        # Best-effort: scan top-level dirs
        for child in repo_root.iterdir():
            if child.is_dir() and (child / "CMakeLists.txt").exists():
                visit(child.name)

    # Catch any orphan CMakeLists not referenced via add_subdirectory.
    # Cap the rglob to a reasonable depth to avoid pathological monorepos.
    skip_dirs = {".git", "build", "_build", "out", "_deps", "cmake-build-debug",
                 "cmake-build-release", "node_modules", ".venv", "venv"}
    if len(out) < max_files:
        for cml in repo_root.rglob("CMakeLists.txt"):
            try:
                rel = cml.resolve().relative_to(repo_root).as_posix()
            except ValueError:
                continue
            if rel in seen:
                continue
            parts = PurePosixPath(rel).parts
            if any(p in skip_dirs for p in parts):
                continue
            if len(out) >= max_files:
                break
            seen.add(rel)
            out.append(parse_cmake_lists(cml, repo_root=repo_root))

    return out


# ---------------------------------------------------------------------------
# CMake File API reply parsing
# ---------------------------------------------------------------------------


def parse_cmake_file_api_reply(repo_root: Path) -> list[CMakeTarget] | None:
    """Parse ``build/.cmake/api/v1/reply/`` JSON if present.

    Returns ``None`` if no reply directory is found. The reply gives the
    authoritative target/source graph (no regex approximation), so callers
    that find it should prefer this output over the text-regex reader.
    """
    reply_root = None
    for candidate in (
        repo_root / "build" / ".cmake" / "api" / "v1" / "reply",
        repo_root / ".cmake" / "api" / "v1" / "reply",
    ):
        if candidate.is_dir():
            reply_root = candidate
            break
    if reply_root is None:
        return None

    targets: list[CMakeTarget] = []
    repo_root_resolved = repo_root.resolve()
    for entry in reply_root.glob("target-*.json"):
        try:
            data = json.loads(entry.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("name", "")
        type_str = str(data.get("type", "")).upper()
        kind_map = {
            "EXECUTABLE": "executable",
            "STATIC_LIBRARY": "library_static",
            "SHARED_LIBRARY": "library_shared",
            "MODULE_LIBRARY": "library_shared",
            "INTERFACE_LIBRARY": "library_interface",
            "OBJECT_LIBRARY": "library_object",
        }
        kind = kind_map.get(type_str, "unknown")

        backtrace = data.get("backtraceGraph", {}).get("files", [])
        cmakelists_rel = ""
        if backtrace:
            try:
                cmakelists_rel = (
                    Path(backtrace[0]).resolve().relative_to(repo_root_resolved).as_posix()
                )
            except (ValueError, OSError):
                cmakelists_rel = ""

        t = CMakeTarget(name=name, kind=kind, cmakelists=cmakelists_rel)
        for src in data.get("sources", []):
            path = src.get("path", "")
            if not path:
                continue
            try:
                abs_path = (repo_root_resolved / path).resolve()
                rel = abs_path.relative_to(repo_root_resolved).as_posix()
            except (ValueError, OSError):
                rel = path
            cls = _classify_path(rel)
            if cls == "header":
                t.private_headers.append(rel)
            elif cls == "source":
                t.sources.append(rel)
        for inc in data.get("compileGroups", []):
            for ig in inc.get("includes", []):
                path = ig.get("path", "")
                if not path:
                    continue
                try:
                    rel = Path(path).resolve().relative_to(repo_root_resolved).as_posix()
                except (ValueError, OSError):
                    rel = path
                t.include_dirs.append(rel)
            for d in inc.get("defines", []):
                define = d.get("define", "")
                macro = define.split("=", 1)[0].strip()
                if macro:
                    t.compile_defines.append(macro)
        for dep in data.get("dependencies", []):
            dep_id = dep.get("id", "")
            if "::@" in dep_id:
                t.link_deps.append(dep_id.split("::@", 1)[0])

        targets.append(t)

    return targets


# ---------------------------------------------------------------------------
# ManifestParser shape — external system records from ``find_package``
# ---------------------------------------------------------------------------


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    """Extract ``find_package(...)`` calls as external-dependency records."""
    cm = parse_cmake_lists(manifest_path, repo_root=repo_root)
    try:
        declared_in = manifest_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        declared_in = manifest_path.as_posix()
    out: list[ExternalSystemRecord] = []
    seen: set[str] = set()
    for pkg, version in cm.find_packages:
        if not pkg or pkg in seen:
            continue
        seen.add(pkg)
        out.append(
            ExternalSystemRecord(
                name=pkg,
                ecosystem=ecosystem,
                declared_in=declared_in,
                version=version,
                display_name=pkg,
                category="library",
                is_dev_dep=False,
            )
        )
    return out


__all__ = [
    "CMakeTarget",
    "CMakeFile",
    "parse_cmake_lists",
    "discover_cmake_reactor",
    "parse_cmake_file_api_reply",
    "parse",
    "filenames",
    "ecosystem",
]

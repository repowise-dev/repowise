"""Filesystem-based tech stack and build command detection.

No DB or network dependencies: reads manifest files at and near the repo root.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Container, Iterator
from itertools import chain
from pathlib import Path

from ...ingestion.composer import COMPOSER_JSON, read_composer
from ...ingestion.framework_facts import detect_php_framework
from ...precedent.structural import declares_ruff_format
from .data import TechStackItem

# package.json dependency -> (display name, category).
_NODE_FRAMEWORKS: dict[str, tuple[str, str]] = {
    "next": ("Next.js", "framework"),
    "react": ("React", "framework"),
    "vue": ("Vue.js", "framework"),
    "svelte": ("Svelte", "framework"),
    "@angular/core": ("Angular", "framework"),
    "express": ("Express", "framework"),
    "fastify": ("Fastify", "framework"),
    "hono": ("Hono", "framework"),
    "nestjs": ("NestJS", "framework"),
    "@nestjs/core": ("NestJS", "framework"),
    "prisma": ("Prisma", "database"),
    "@prisma/client": ("Prisma", "database"),
    "drizzle-orm": ("Drizzle ORM", "database"),
    "typeorm": ("TypeORM", "database"),
    "mongoose": ("Mongoose", "database"),
    "sequelize": ("Sequelize", "database"),
    "tailwindcss": ("Tailwind CSS", "framework"),
    "vite": ("Vite", "infra"),
    "webpack": ("Webpack", "infra"),
    "turbo": ("Turborepo", "infra"),
}

# Keyword searched for in pyproject.toml -> (display name, category).
_PYTHON_FRAMEWORKS: dict[str, tuple[str, str]] = {
    "fastapi": ("FastAPI", "framework"),
    "django": ("Django", "framework"),
    "flask": ("Flask", "framework"),
    "starlette": ("Starlette", "framework"),
    "litestar": ("Litestar", "framework"),
    "sqlalchemy": ("SQLAlchemy", "database"),
    "alembic": ("Alembic", "database"),
    "celery": ("Celery", "infra"),
    "pydantic": ("Pydantic", "framework"),
    "aiohttp": ("aiohttp", "framework"),
    "httpx": ("HTTPX", "framework"),
    "torch": ("PyTorch", "framework"),
    "tensorflow": ("TensorFlow", "framework"),
}


# Deep enough for `services/<svc>/src/<Project>/<Project>.csproj` (depth 5);
# deeper only reaches samples and tests buried in generated SDK folders.
_DOTNET_MAX_DEPTH = 5

# A representative sample is enough to infer the stack of a repo with
# thousands of project files.
_DOTNET_MAX_PROJECTS = 200

# Build output and tooling dirs never hold real project source. Test fixtures
# and samples are pruned too, so a repo is not labelled C# by its own test data.
_DOTNET_PRUNE = frozenset({
    "bin", "obj", ".vs", "node_modules", ".git", "packages",
    ".idea", "artifacts", ".build", "TestResults",
    "tests", "test", "fixtures", "test-repos", "testdata", "samples",
    "local-stash",
})


def _find_dotnet_projects(repo_path: Path) -> list[Path]:
    """Return up to ``_DOTNET_MAX_PROJECTS`` .csproj files under *repo_path*.

    Bounded depth-first walk that prunes build-output and tooling
    directories. Order is depth-first but stable across runs (sorted
    children at each level) so caching downstream is deterministic.
    """
    found: list[Path] = []

    def _walk(current: Path, depth: int) -> None:
        if len(found) >= _DOTNET_MAX_PROJECTS:
            return
        if depth > _DOTNET_MAX_DEPTH:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except (OSError, PermissionError):
            return
        for entry in entries:
            if len(found) >= _DOTNET_MAX_PROJECTS:
                return
            if entry.is_dir():
                if entry.name in _DOTNET_PRUNE or entry.name.startswith("."):
                    continue
                # A nested git repo is a separate project; its files must not
                # define this repo's stack.
                if (entry / ".git").exists():
                    continue
                _walk(entry, depth + 1)
            elif entry.is_file() and entry.suffix == ".csproj":
                found.append(entry)

    _walk(repo_path, 0)
    return found


# Every root-level path the scan below reads by name. The memo key stats all of
# them, so adding a read here means adding it there or the memo goes stale on
# the signal you just added.
_MANIFEST_FILES = (
    "package.json",
    "tsconfig.json",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Directory.Build.props",
    "Directory.Packages.props",
)

# repo_path -> (manifest fingerprint, detected stack).
_STACK_CACHE: dict[str, tuple[tuple, list[TechStackItem]]] = {}


def _manifest_fingerprint(repo_path: Path) -> tuple:
    """Cheap stat-based signature of the root inputs this scan reads.

    A few stats and one root glob, versus the bounded .csproj walk that dominates
    the real scan. ``*.sln`` is globbed because the scan reads solutions by
    pattern, not by name. The trailing directory mtime is a bonus, not the
    mechanism: Windows timestamps are quantized to the ~15.6ms timer tick.

    CEILING: a nested change (a new ``.csproj`` in an existing subdirectory, a
    workspace ``tsconfig.json`` one or two levels down) does not move the key, so
    a long-lived process re-indexing the same repo can be served a stale stack.
    The stack is contextual metadata only. To close it, key on a traversal
    snapshot instead of the root.
    """
    sig: list = []
    for name in _MANIFEST_FILES:
        try:
            st = (repo_path / name).stat()
            sig.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((name, None, None))
    try:
        sig.extend(sorted((p.name, p.stat().st_mtime_ns) for p in repo_path.glob("*.sln")))
    except OSError:
        sig.append(("*.sln", None))
    try:
        sig.append(("", repo_path.stat().st_mtime_ns, None))
    except OSError:
        sig.append(("", None, None))
    return tuple(sig)


def detect_tech_stack(repo_path: Path) -> list[TechStackItem]:
    """Detect languages, frameworks, and infra tools from manifest files.

    Scans repo root and one level deep for common manifest files.
    Returns items sorted by category then name.

    Memoized on the root inputs' stat signature: a single ``repowise update``
    asks twice (the graph's framework edges, then the knowledge-graph refresh)
    and gets the same answer both times off an unchanged tree. See
    :func:`_manifest_fingerprint` for what the key does and does not cover.
    """
    key = str(Path(repo_path).resolve())
    fingerprint = _manifest_fingerprint(Path(repo_path))
    cached = _STACK_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return list(cached[1])
    items_list = _detect_tech_stack_uncached(Path(repo_path))
    _STACK_CACHE[key] = (fingerprint, items_list)
    return list(items_list)


_Item = tuple[str, str | None, str]

# A root package.json with none of these fields, no engines.node and no known
# framework is tooling (a test runner, git hooks) rather than a Node.js app.
_NODE_RUNTIME_FIELDS = ("dependencies", "main", "bin", "module", "exports")

# Root files whose mere presence names a technology.
_ROOT_MARKERS: tuple[tuple[tuple[str, ...], _Item], ...] = (
    (("Cargo.toml",), ("Rust", None, "language")),
    (("Gemfile",), ("Ruby", None, "language")),
    (("Dockerfile",), ("Docker", None, "infra")),
    (("docker-compose.yml", "docker-compose.yaml"), ("Docker Compose", None, "infra")),
)

# .NET stack indicators, tested against the joined text of the scanned .csproj
# files and the stems of every .csproj found.
_DOTNET_FLAVOURS: tuple[tuple[str, str, Callable[[str, list[str]], bool]], ...] = (
    ("ASP.NET Core", "framework", lambda text, stems: "Microsoft.AspNetCore" in text),
    (
        "Entity Framework Core",
        "database",
        lambda text, stems: "Microsoft.EntityFrameworkCore" in text,
    ),
    (
        ".NET Aspire",
        "infra",
        lambda text, stems: "Aspire.Hosting" in text or any("AppHost" in s for s in stems),
    ),
    (
        "gRPC",
        "framework",
        lambda text, stems: "Grpc.AspNetCore" in text or "Google.Protobuf" in text,
    ),
    (
        ".NET MAUI",
        "framework",
        lambda text, stems: "MAUI" in text.upper() or any("Maui" in s for s in stems),
    ),
    (
        "WinUI 3",
        "framework",
        lambda text, stems: "Microsoft.WindowsAppSDK" in text or "Microsoft.UI.Xaml" in text,
    ),
    (
        "WPF",
        "framework",
        lambda text, stems: "Microsoft.NET.Sdk.WindowsDesktop" in text or "<UseWPF>true" in text,
    ),
    (
        "Windows Forms",
        "framework",
        lambda text, stems: (
            "Microsoft.NET.Sdk.WindowsDesktop" in text and "<UseWindowsForms>true" in text
        ),
    ),
)


def _detect_tech_stack_uncached(repo_path: Path) -> list[TechStackItem]:
    """The real scan. See :func:`detect_tech_stack` for the contract."""
    items: dict[str, TechStackItem] = {}
    for detect in _DETECTORS:
        for name, version, category in detect(repo_path):
            if name not in items:
                items[name] = TechStackItem(name=name, version=version, category=category)
    return sorted(items.values(), key=lambda x: (x.category, x.name))


def _read_package_json(repo_path: Path) -> object:
    """Parsed root package.json, or None when it is absent or unreadable."""
    try:
        return json.loads((repo_path / "package.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def _node_items(repo_path: Path) -> Iterator[_Item]:
    pkg = _read_package_json(repo_path)
    if not isinstance(pkg, dict):
        return
    all_deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if _is_node_app(pkg, all_deps):
        yield "Node.js", _node_engine(pkg), "language"
        yield from _node_framework_items(all_deps)
    # TypeScript stands on its own: many repos use it through tsconfig.json alone.
    if "typescript" in all_deps or _has_tsconfig(repo_path):
        yield "TypeScript", _dep_version(all_deps, "typescript"), "language"


def _node_framework_items(all_deps: dict) -> Iterator[_Item]:
    for dep_key, (display, cat) in _NODE_FRAMEWORKS.items():
        if dep_key in all_deps:
            yield display, _dep_version(all_deps, dep_key), cat


def _dep_version(all_deps: dict, name: str) -> str | None:
    """The declared version with its range operator stripped, or None."""
    return all_deps.get(name, "").lstrip("^~>=") or None


def _node_engine(pkg: dict) -> str | None:
    engines = pkg.get("engines")
    return engines.get("node") if isinstance(engines, dict) else None


def _is_node_app(pkg: dict, all_deps: dict) -> bool:
    return bool(
        any(pkg.get(field) for field in _NODE_RUNTIME_FIELDS)
        or _node_engine(pkg)
        or any(dep_key in all_deps for dep_key in _NODE_FRAMEWORKS)
    )


def _has_tsconfig(repo_path: Path) -> bool:
    """A tsconfig.json at the root or in a workspace package up to two levels down."""
    if (repo_path / "tsconfig.json").exists():
        return True
    nested = chain(repo_path.glob("*/tsconfig.json"), repo_path.glob("*/*/tsconfig.json"))
    return any(_is_project_dir(repo_path, p) for p in nested)


def _is_project_dir(repo_path: Path, path: Path) -> bool:
    """False when *path* sits under node_modules, a dot dir, or a nested git repo."""
    rel_parts = path.relative_to(repo_path).parts[:-1]
    if any(part == "node_modules" or part.startswith(".") for part in rel_parts):
        return False
    probe = repo_path
    for part in rel_parts:
        probe = probe / part
        if (probe / ".git").exists():
            return False
    return True


def _python_items(repo_path: Path) -> Iterator[_Item]:
    pyproject = repo_path / "pyproject.toml"
    if not (pyproject.exists() or (repo_path / "setup.py").exists()):
        return
    yield "Python", None, "language"
    if not pyproject.exists():
        return
    text = pyproject.read_text(encoding="utf-8").lower()
    for dep_key, (display, cat) in _PYTHON_FRAMEWORKS.items():
        if dep_key in text:
            yield display, None, cat


def _go_items(repo_path: Path) -> Iterator[_Item]:
    go_mod = repo_path / "go.mod"
    if go_mod.exists():
        ver_match = re.search(r"^go\s+(\S+)", go_mod.read_text(encoding="utf-8"), re.MULTILINE)
        yield "Go", ver_match.group(1) if ver_match else None, "language"


def _jvm_items(repo_path: Path) -> Iterator[_Item]:
    if (repo_path / "pom.xml").exists():
        yield "Java", None, "language"
        yield "Maven", None, "infra"
    kotlin_dsl = (repo_path / "build.gradle.kts").exists()
    if kotlin_dsl or (repo_path / "build.gradle").exists():
        yield "Kotlin" if kotlin_dsl else "Java", None, "language"
        yield "Gradle", None, "infra"


def _php_items(repo_path: Path) -> Iterator[_Item]:
    composer_json = repo_path / COMPOSER_JSON
    if not composer_json.exists():
        return
    yield "PHP", None, "language"
    composer = read_composer(composer_json)
    framework = detect_php_framework(composer) if composer is not None else None
    if framework is not None:
        yield framework.name, None, "framework"


def _dotnet_items(repo_path: Path) -> Iterator[_Item]:
    # A bounded walk, not a shallow glob: .NET monorepos keep projects under
    # `src/modules/<module>/` or `services/<svc>/`.
    csproj_files = _find_dotnet_projects(repo_path)
    if not (csproj_files or _has_solution(repo_path) or _has_directory_build(repo_path)):
        return
    target_fw = _target_framework(csproj_files[:10])
    yield "C#", target_fw, "language"
    yield ".NET", target_fw, "framework"
    # The cap counts files, not bytes: repos with a hundred-plus small projects
    # need a generous one before they look like an unflavoured .NET repo.
    joined_csproj = "".join(_read_csproj_texts(csproj_files[:80]))
    stems = [p.stem for p in csproj_files]
    for name, category, matches in _DOTNET_FLAVOURS:
        if matches(joined_csproj, stems):
            yield name, None, category


def _has_solution(repo_path: Path) -> bool:
    """A .sln at the root, or one level down outside pruned dirs and nested repos."""
    if any(repo_path.glob("*.sln")):
        return True
    return any(
        sln.parent.name not in _DOTNET_PRUNE and not (sln.parent / ".git").exists()
        for sln in repo_path.glob("*/*.sln")
    )


def _has_directory_build(repo_path: Path) -> bool:
    return any(
        (repo_path / name).exists()
        for name in ("Directory.Build.props", "Directory.Packages.props")
    )


def _read_csproj_texts(csproj_files: list[Path]) -> Iterator[str]:
    for csproj in csproj_files:
        try:
            yield csproj.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def _target_framework(csproj_files: list[Path]) -> str | None:
    """TargetFramework(s) of the first project declaring one (net9.0, net8.0, ...)."""
    for text in _read_csproj_texts(csproj_files):
        m = re.search(r"<TargetFrameworks?>\s*([^<;]+)", text)
        if m:
            return m.group(1).strip()
    return None


def _marker_items(repo_path: Path) -> Iterator[_Item]:
    for names, item in _ROOT_MARKERS:
        if any((repo_path / name).exists() for name in names):
            yield item


# Order matters only for first-wins on a name two detectors both emit.
_DETECTORS: tuple[Callable[[Path], Iterator[_Item]], ...] = (
    _node_items,
    _python_items,
    _go_items,
    _jvm_items,
    _php_items,
    _dotnet_items,
    _marker_items,
)

# Command key -> script names to try, in order of preference.
_NPM_SCRIPTS: dict[str, tuple[str, ...]] = {
    "build": ("build",),
    "test": ("test", "jest", "vitest"),
    "lint": ("lint",),
    "dev": ("dev", "start:dev", "start"),
    "format": ("format", "prettier"),
    "typecheck": ("typecheck", "type-check", "tsc"),
}

_MAKE_TARGETS: dict[str, tuple[str, ...]] = {
    "build": ("build",),
    "test": ("test", "tests"),
    "lint": ("lint",),
    "dev": ("dev", "run"),
    "format": ("fmt", "format"),
}

_MAKE_TARGET_RE = re.compile(r"^([a-z][a-z0-9_-]*):", re.MULTILINE)

# Lockfile -> script runner, first present wins.
_LOCKFILE_RUNNERS = (
    ("bun.lock", "bun run"),
    ("bun.lockb", "bun run"),
    ("yarn.lock", "yarn"),
    ("pnpm-lock.yaml", "pnpm"),
)


def detect_build_commands(repo_path: Path) -> dict[str, str]:
    """Detect common build/test/lint commands from manifest files.

    Returns a dict with keys from: build, test, lint, dev, format, typecheck.
    Only includes keys where a command was actually detected.
    """
    commands = _package_script_commands(repo_path)
    _add_pyproject_commands(repo_path, commands)
    for key, command in _make_commands(repo_path).items():
        commands.setdefault(key, command)
    return commands


def _first_available(
    candidates: dict[str, tuple[str, ...]], available: Container[str], runner: str
) -> dict[str, str]:
    """Each key mapped to ``"<runner> <name>"`` for its first available candidate name."""
    found: dict[str, str] = {}
    for key, names in candidates.items():
        match = next((name for name in names if name in available), None)
        if match is not None:
            found[key] = f"{runner} {match}"
    return found


def _package_script_commands(repo_path: Path) -> dict[str, str]:
    pkg = _read_package_json(repo_path)
    runner = next(
        (cmd for lockfile, cmd in _LOCKFILE_RUNNERS if (repo_path / lockfile).exists()),
        "npm run",
    )
    try:
        return _first_available(_NPM_SCRIPTS, pkg.get("scripts", {}), runner)
    except (AttributeError, TypeError):
        # No manifest, a manifest that is not an object, or non-container scripts.
        return {}


def _add_pyproject_commands(repo_path: Path, commands: dict[str, str]) -> None:
    pyproject = repo_path / "pyproject.toml"
    if not pyproject.exists():
        return
    text = pyproject.read_text(encoding="utf-8")
    if "pytest" in text:
        commands.setdefault("test", "pytest")
    if "ruff" in text:
        commands.setdefault("lint", "ruff check .")
    if "format" not in commands and declares_ruff_format(repo_path):
        commands["format"] = "ruff format ."
    if "mypy" in text:
        commands.setdefault("typecheck", "mypy .")


def _make_commands(repo_path: Path) -> dict[str, str]:
    try:
        text = (repo_path / "Makefile").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    return _first_available(_MAKE_TARGETS, set(_MAKE_TARGET_RE.findall(text)), "make")

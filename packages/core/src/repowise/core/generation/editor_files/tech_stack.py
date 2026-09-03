"""Filesystem-based tech stack and build command detection.

No DB or network dependencies — scans manifest files in the repo root.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .data import TechStackItem

# Node.js framework/library signatures to detect from package.json dependencies
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

# Python framework/library keywords in pyproject.toml / requirements.txt
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


# Maximum directory depth from the repo root to scan for .NET project
# files. Five levels covers every observed .NET monorepo layout in the
# wild (e.g. `src/<area>/<module>/<Project>/<Project>.csproj` is depth
# 4; `services/<svc>/src/<Project>/<Project>.csproj` is depth 5).
# Setting this higher would only add noise from samples / tests buried
# inside generated SDK folders.
_DOTNET_MAX_DEPTH = 5

# Hard cap on returned .csproj count. Repos like dotnet/runtime have
# thousands of project files; we only need a representative sample to
# infer the tech stack.
_DOTNET_MAX_PROJECTS = 200

# Directory names to prune from the scan. These never host real
# project source and bloat the walk on Windows where `bin/obj`
# contains thousands of intermediate files per project. Test fixtures
# and vendored sample repos are pruned too: a Python/TS repo that keeps
# .NET solutions under tests/fixtures/ or test-repos/ must not be
# labelled a C# codebase by its own test data.
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
                # A nested git repo is a separate project (vendored
                # benchmark checkout, sibling clone) — its project files
                # must not define THIS repo's tech stack.
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
    # .NET evidence read at the root, same as the rest.
    "Directory.Build.props",
    "Directory.Packages.props",
)

# repo_path -> (manifest fingerprint, detected stack).
_STACK_CACHE: dict[str, tuple[tuple, list[TechStackItem]]] = {}


def _manifest_fingerprint(repo_path: Path) -> tuple:
    """Cheap stat-based signature of the root inputs this scan reads.

    Sixteen stats plus one root glob, versus the bounded .csproj walk that
    dominates the real scan (0.18s on hugo, 0.47s on PowerToys, measured). The
    ``*.sln`` glob is in the key because the scan reads solutions by pattern
    rather than by name, so no fixed entry can stand in for them.

    The trailing directory-mtime entry is a bonus, not the mechanism: on Windows
    file timestamps come from the ~15.6ms system timer tick, so two changes
    inside one tick can leave it byte-identical. Every root path the scan reads
    is stat'd by name or globbed above, so the key does not depend on it.

    CEILING: it does not see a nested change - a ``.csproj`` appearing under an
    existing subdirectory, or a workspace ``tsconfig.json`` one or two levels
    down (``glob("*/tsconfig.json")`` and ``"*/*/tsconfig.json"``). Covering
    those means walking, which is the cost this exists to avoid. In one CLI
    command the window is seconds, so it is unreachable there; a long-lived
    process (the server's job executor, or a test suite driving several
    ``CliRunner`` invocations in one interpreter) can re-index the same repo
    later and be served a stale stack. Blast radius is contextual metadata only:
    framework edges, the knowledge-graph tech list, the editor file table. To
    close it, key on a traversal snapshot instead of the root.
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


def _detect_tech_stack_uncached(repo_path: Path) -> list[TechStackItem]:
    """The real scan. See :func:`detect_tech_stack` for the contract."""
    items: dict[str, TechStackItem] = {}

    def add(name: str, version: str | None, category: str) -> None:
        if name not in items:
            items[name] = TechStackItem(name=name, version=version, category=category)

    # --- package.json (Node.js) ---
    # Many .NET / Python / Go repos drop a package.json at the root for
    # tooling like Playwright or Husky without being Node.js applications.
    # We only register Node.js as a language when there is real evidence
    # of a Node.js runtime: a ``main``/``bin`` field, runtime
    # ``dependencies``, or a known framework dep.
    pkg_json = repo_path / "package.json"
    pkg: dict[str, object] | None = None
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
        except Exception:
            pkg = None

    if isinstance(pkg, dict):
        runtime_deps = pkg.get("dependencies") or {}
        dev_deps = pkg.get("devDependencies") or {}
        all_deps = {**runtime_deps, **dev_deps}
        node_ver = (pkg.get("engines") or {}).get("node") if isinstance(pkg.get("engines"), dict) else None
        # Tooling-only manifests (e.g. .NET / Python repos that drop a
        # package.json for Playwright or Husky) declare no runtime
        # dependencies, no entry-point fields, and no engines hint. We
        # gate the "Node.js" language tag on at least one of those
        # signals to keep them from being labelled Node.js apps.
        has_runtime_signal = bool(
            runtime_deps
            or pkg.get("main")
            or pkg.get("bin")
            or pkg.get("module")
            or pkg.get("exports")
            or node_ver
        )
        has_framework_dep = any(dep_key in all_deps for dep_key in _NODE_FRAMEWORKS)
        if has_runtime_signal or has_framework_dep:
            add("Node.js", node_ver, "language")
            for dep_key, (display, cat) in _NODE_FRAMEWORKS.items():
                if dep_key in all_deps:
                    raw = all_deps[dep_key].lstrip("^~>=")
                    add(display, raw or None, cat)
        # TypeScript can be added independently — many monorepos only use
        # TS via tsconfig.json without depending on a Node.js runtime.
        # Monorepos frequently keep tsconfig.json only inside workspace
        # packages (packages/*/tsconfig.json), so look two levels deep.
        def _is_project_dir(p: Path) -> bool:
            rel_parts = p.relative_to(repo_path).parts[:-1]
            if any(part == "node_modules" or part.startswith(".") for part in rel_parts):
                return False
            # Skip nested git repos (sibling clones, vendored checkouts).
            probe = repo_path
            for part in rel_parts:
                probe = probe / part
                if (probe / ".git").exists():
                    return False
            return True

        has_tsconfig = (
            (repo_path / "tsconfig.json").exists()
            or any(_is_project_dir(p) for p in repo_path.glob("*/tsconfig.json"))
            or any(_is_project_dir(p) for p in repo_path.glob("*/*/tsconfig.json"))
        )
        if "typescript" in all_deps or has_tsconfig:
            ts_ver = all_deps.get("typescript", "").lstrip("^~>=") or None
            add("TypeScript", ts_ver, "language")

    # --- pyproject.toml / setup.py (Python) ---
    pyproject = repo_path / "pyproject.toml"
    setup_py = repo_path / "setup.py"
    if pyproject.exists() or setup_py.exists():
        add("Python", None, "language")
        if pyproject.exists():
            text = pyproject.read_text(encoding="utf-8").lower()
            for dep_key, (display, cat) in _PYTHON_FRAMEWORKS.items():
                if dep_key in text:
                    add(display, None, cat)

    # --- Cargo.toml (Rust) ---
    if (repo_path / "Cargo.toml").exists():
        add("Rust", None, "language")

    # --- go.mod (Go) ---
    go_mod = repo_path / "go.mod"
    if go_mod.exists():
        text = go_mod.read_text(encoding="utf-8")
        ver_match = re.search(r"^go\s+(\S+)", text, re.MULTILINE)
        add("Go", ver_match.group(1) if ver_match else None, "language")

    # --- pom.xml / build.gradle (Java/Kotlin) ---
    if (repo_path / "pom.xml").exists():
        add("Java", None, "language")
        add("Maven", None, "infra")
    if (repo_path / "build.gradle").exists() or (repo_path / "build.gradle.kts").exists():
        add("Kotlin" if (repo_path / "build.gradle.kts").exists() else "Java", None, "language")
        add("Gradle", None, "infra")

    # --- Gemfile (Ruby) ---
    if (repo_path / "Gemfile").exists():
        add("Ruby", None, "language")

    # --- composer.json (PHP) ---
    composer_json = repo_path / "composer.json"
    if composer_json.exists():
        add("PHP", None, "language")
        try:
            composer = json.loads(composer_json.read_text(encoding="utf-8"))
        except Exception:
            composer = None
        if isinstance(composer, dict):
            requires = {
                **(composer.get("require") or {}),
                **(composer.get("require-dev") or {}),
            }
            if (
                composer.get("type") == "typo3-cms-extension"
                or "typo3/cms-core" in requires
            ):
                add("TYPO3", None, "framework")
            elif "symfony/framework-bundle" in requires or "symfony/symfony" in requires:
                add("Symfony", None, "framework")
            elif "laravel/framework" in requires:
                add("Laravel", None, "framework")

    # --- .NET / C# (.csproj / .sln / Directory.Build.props) ---
    # Walk the tree (bounded) so monorepos whose projects live under
    # `src/modules/<module>/<Module>.csproj` or `services/foo/foo.csproj`
    # still register. A shallow glob misses every real-world .NET
    # monorepo layout — eShop, Aspire samples, PowerToys, Roslyn etc.
    csproj_files = _find_dotnet_projects(repo_path)
    sln_files = [
        sln
        for sln in list(repo_path.glob("*.sln")) + list(repo_path.glob("*/*.sln"))
        if sln.parent == repo_path
        or (
            sln.parent.name not in _DOTNET_PRUNE
            and not (sln.parent / ".git").exists()
        )
    ]
    has_directory_build = (repo_path / "Directory.Build.props").exists() or (
        repo_path / "Directory.Packages.props"
    ).exists()
    if csproj_files or sln_files or has_directory_build:
        # Pull TargetFramework from the first .csproj — captures net9.0,
        # net8.0, etc. Best-effort regex; the .csproj XML is small so a
        # full parser would be overkill.
        target_fw: str | None = None
        for csproj in csproj_files[:10]:
            try:
                ctext = csproj.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            m = re.search(
                r"<TargetFrameworks?>\s*([^<;]+)", ctext
            )
            if m:
                target_fw = m.group(1).strip()
                break
        add("C#", target_fw, "language")
        add(".NET", target_fw, "framework")
        # Common .NET stack indicators read from any .csproj text. The
        # cap is per-file, not per-byte — small projects with many
        # csprojs (PowerToys ~140, Roslyn ~300) need a generous limit
        # before they look like an unflavoured .NET repo.
        joined_csproj = ""
        for csproj in csproj_files[:80]:
            try:
                joined_csproj += csproj.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        if "Microsoft.AspNetCore" in joined_csproj:
            add("ASP.NET Core", None, "framework")
        if "Microsoft.EntityFrameworkCore" in joined_csproj:
            add("Entity Framework Core", None, "database")
        if "Aspire.Hosting" in joined_csproj or any(
            "AppHost" in p.stem for p in csproj_files
        ):
            add(".NET Aspire", None, "infra")
        if "Grpc.AspNetCore" in joined_csproj or "Google.Protobuf" in joined_csproj:
            add("gRPC", None, "framework")
        if "MAUI" in joined_csproj.upper() or any(
            "Maui" in p.stem for p in csproj_files
        ):
            add(".NET MAUI", None, "framework")
        if "Microsoft.WindowsAppSDK" in joined_csproj or "Microsoft.UI.Xaml" in joined_csproj:
            add("WinUI 3", None, "framework")
        if "Microsoft.NET.Sdk.WindowsDesktop" in joined_csproj or "<UseWPF>true" in joined_csproj:
            add("WPF", None, "framework")
        if "Microsoft.NET.Sdk.WindowsDesktop" in joined_csproj and "<UseWindowsForms>true" in joined_csproj:
            add("Windows Forms", None, "framework")

    # --- Docker ---
    if (repo_path / "Dockerfile").exists():
        add("Docker", None, "infra")
    if (repo_path / "docker-compose.yml").exists() or (repo_path / "docker-compose.yaml").exists():
        add("Docker Compose", None, "infra")

    return sorted(items.values(), key=lambda x: (x.category, x.name))


def detect_build_commands(repo_path: Path) -> dict[str, str]:
    """Detect common build/test/lint commands from manifest files.

    Returns a dict with keys from: build, test, lint, dev, format, typecheck.
    Only includes keys where a command was actually detected.
    """
    commands: dict[str, str] = {}

    # --- package.json scripts ---
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            _map = {
                "build": ["build"],
                "test": ["test", "jest", "vitest"],
                "lint": ["lint"],
                "dev": ["dev", "start:dev", "start"],
                "format": ["format", "prettier"],
                "typecheck": ["typecheck", "type-check", "tsc"],
            }
            runner = "npm run" if not (repo_path / "pnpm-lock.yaml").exists() else "pnpm"
            if (repo_path / "yarn.lock").exists():
                runner = "yarn"
            if (repo_path / "bun.lock").exists() or (repo_path / "bun.lockb").exists():
                runner = "bun run"
            for key, candidates in _map.items():
                for cand in candidates:
                    if cand in scripts:
                        commands[key] = f"{runner} {cand}"
                        break
        except Exception:
            pass

    # --- pyproject.toml ---
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        if "test" not in commands and ("pytest" in text or "[tool.pytest" in text):
            commands["test"] = "pytest"
        if "lint" not in commands and "ruff" in text:
            commands["lint"] = "ruff check ."
        if "format" not in commands and "ruff" in text and "format" in text:
            commands["format"] = "ruff format ."
        if "typecheck" not in commands and "mypy" in text:
            commands["typecheck"] = "mypy ."

    # --- Makefile (first-level .PHONY or obvious targets) ---
    makefile = repo_path / "Makefile"
    if makefile.exists():
        try:
            mk_text = makefile.read_text(encoding="utf-8")
            target_pat = re.compile(r"^([a-z][a-z0-9_-]*):", re.MULTILINE)
            mk_targets = set(target_pat.findall(mk_text))
            _make_map = {
                "build": ["build"],
                "test": ["test", "tests"],
                "lint": ["lint"],
                "dev": ["dev", "run"],
                "format": ["fmt", "format"],
            }
            for key, candidates in _make_map.items():
                if key not in commands:
                    for cand in candidates:
                        if cand in mk_targets:
                            commands[key] = f"make {cand}"
                            break
        except Exception:
            pass

    return commands

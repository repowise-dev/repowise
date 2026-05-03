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


def detect_tech_stack(repo_path: Path) -> list[TechStackItem]:
    """Detect languages, frameworks, and infra tools from manifest files.

    Scans repo root and one level deep for common manifest files.
    Returns items sorted by category then name.
    """
    items: dict[str, TechStackItem] = {}

    def add(name: str, version: str | None, category: str) -> None:
        if name not in items:
            items[name] = TechStackItem(name=name, version=version, category=category)

    # --- package.json (Node.js) ---
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            # Detect Node version from engines field
            node_ver = pkg.get("engines", {}).get("node")
            add("Node.js", node_ver, "language")
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for dep_key, (display, cat) in _NODE_FRAMEWORKS.items():
                if dep_key in all_deps:
                    raw = all_deps[dep_key].lstrip("^~>=")
                    add(display, raw or None, cat)
            if "typescript" in all_deps or (repo_path / "tsconfig.json").exists():
                ts_ver = all_deps.get("typescript", "").lstrip("^~>=") or None
                add("TypeScript", ts_ver, "language")
        except Exception:
            pass

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

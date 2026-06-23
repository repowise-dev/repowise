"""Role descriptions for well-known config/doc/tooling files.

A deterministic, zero-cost lookup that turns a recognised filename into a real
one-line role ("Python project metadata and build configuration.") instead of a
bare restatement of its name ("Configuration file: pyproject.toml."). The cheap
summary floor and any other fallback path consult this first so a reader or
agent learns what a file *is for* rather than re-reading the name they already
have.

Pure and side-effect free: a single ``well_known_role(filename)`` lookup keyed
on the basename, with a small suffix fallback for whole families (lockfiles,
CI workflow files). Returns ``None`` when the name is not recognised, so callers
keep their existing fallback for genuinely opaque files.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# Exact basename (lowercased) -> role sentence. Covers the conventional project
# scaffolding that shows up in almost every repo, where the filename alone is
# the whole identity and a "Configuration file: X" summary adds nothing.
_BY_NAME: dict[str, str] = {
    # Python packaging / tooling
    "pyproject.toml": "Python project metadata, dependencies, and build configuration.",
    "setup.py": "Python package build script (setuptools).",
    "setup.cfg": "Python packaging and tool configuration.",
    "requirements.txt": "Pinned Python dependency list.",
    "tox.ini": "Tox test-automation and environment matrix configuration.",
    "ruff.toml": "Ruff linter and formatter configuration.",
    "mypy.ini": "MyPy static type-checking configuration.",
    "pytest.ini": "Pytest configuration.",
    "conftest.py": "Shared pytest fixtures and test configuration.",
    "alembic.ini": "Alembic database-migration configuration.",
    # Node / JS / TS
    "package.json": "Node package manifest: dependencies, scripts, and metadata.",
    "package-lock.json": "Resolved npm dependency lockfile.",
    "tsconfig.json": "TypeScript compiler configuration.",
    "vite.config.ts": "Vite build and dev-server configuration.",
    "vite.config.js": "Vite build and dev-server configuration.",
    "next.config.js": "Next.js framework configuration.",
    "next.config.mjs": "Next.js framework configuration.",
    "tailwind.config.ts": "Tailwind CSS design-token and theme configuration.",
    "tailwind.config.js": "Tailwind CSS design-token and theme configuration.",
    "eslint.config.js": "ESLint linting rules.",
    ".eslintrc.json": "ESLint linting rules.",
    ".prettierrc": "Prettier formatting configuration.",
    # Containers / infra
    "dockerfile": "Container image build definition.",
    "docker-compose.yml": "Multi-container service orchestration for local development.",
    "docker-compose.yaml": "Multi-container service orchestration for local development.",
    ".dockerignore": "Files excluded from the Docker build context.",
    "makefile": "Build, test, and task automation targets.",
    # VCS / repo hygiene
    ".gitignore": "Paths excluded from version control.",
    ".gitattributes": "Per-path Git behaviour (line endings, diff, linguist).",
    ".editorconfig": "Editor formatting conventions shared across the team.",
    ".pre-commit-config.yaml": "Pre-commit hook definitions run before each commit.",
    # Project docs / governance
    "readme.md": "Project overview and entry point for new readers.",
    "contributing.md": "How to contribute: workflow, standards, and review process.",
    "security.md": "Security policy and vulnerability-reporting process.",
    "code_of_conduct.md": "Community code of conduct.",
    "license": "Project license terms.",
    "license.md": "Project license terms.",
    "changelog.md": "Release history and notable changes.",
    "codeowners": "Path-to-reviewer ownership mapping.",
    "pull_request_template.md": "Template prompting authors for PR description and checklist.",
    "bug_report.md": "Issue template for reporting bugs.",
    "feature_request.md": "Issue template for proposing features.",
    "funding.yml": "Sponsorship and funding links for the repository.",
    # Agent / editor tooling
    "marketplace.json": "Plugin marketplace listing and metadata.",
    "plugin.json": "Plugin manifest: identity, entry points, and capabilities.",
    "hooks.json": "Plugin lifecycle hook definitions.",
    "claude.md": "Repository instructions and context for AI coding agents.",
}

# Whole-family fallbacks keyed on a filename suffix, tried when the exact name
# is not mapped. Ordered most-specific first.
_BY_SUFFIX: tuple[tuple[str, str], ...] = (
    (".lock", "Resolved dependency lockfile pinning exact versions."),
)

# Directory-context fallbacks: a recognised tooling directory gives every file
# beneath it a role even when the individual filename is project-specific (CI
# workflow names vary, but they are all workflow definitions). Keyed on an
# ordered tuple of path segments that must appear contiguously.
_BY_DIR: tuple[tuple[tuple[str, ...], str], ...] = (
    ((".github", "workflows"), "GitHub Actions CI/CD workflow definition."),
)


def well_known_role(path: str) -> str | None:
    """Return a role sentence for a recognised file at *path*, else ``None``.

    *path* may be a bare name or a full path. The basename is matched first
    (case-insensitive), then a suffix family, then the enclosing directory
    convention. Returns ``None`` when nothing is recognised, so callers keep
    their existing fallback for genuinely opaque files.
    """
    name = PurePosixPath(path).name.lower()
    role = _BY_NAME.get(name)
    if role is not None:
        return role
    for suffix, dir_role in _BY_SUFFIX:
        if name.endswith(suffix):
            return dir_role
    segments = tuple(s.lower() for s in PurePosixPath(path).parts[:-1])
    for needle, dir_role in _BY_DIR:
        span = len(needle)
        if span <= len(segments) and any(
            segments[i : i + span] == needle for i in range(len(segments) - span + 1)
        ):
            return dir_role
    return None

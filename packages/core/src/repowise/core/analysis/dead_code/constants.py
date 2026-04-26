"""Static configuration for dead-code detection.

These tuples / frozensets shape what the analyzer treats as "always
alive" (framework decorators, never-flag path globs) and where to skip
entirely (test fixture directories, non-code languages).
"""

from __future__ import annotations

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY


# Non-code languages that should never be flagged as dead code.
# Derived from the centralised LanguageRegistry — passthrough config/infra
# languages plus "unknown".
_NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    spec.tag
    for spec in _LANG_REGISTRY.all_specs()
    if spec.is_passthrough and (not spec.is_code or spec.is_infra) and spec.tag != "openapi"
) | {"unknown"}

# Patterns that should never be flagged as dead.
_NEVER_FLAG_PATTERNS: tuple[str, ...] = (
    "*__init__.py",
    "*__main__.py",
    "*conftest.py",
    "*alembic/env.py",
    "*manage.py",
    "*wsgi.py",
    "*asgi.py",
    "*migrations*",
    "*schema*",
    "*seed*",
    "*.d.ts",
    "*setup.py",
    "*setup.cfg",
    "*next.config.*",
    "*vite.config.*",
    "*tailwind.config.*",
    "*postcss.config.*",
    "*jest.config.*",
    "*vitest.config.*",
    # Next.js / Remix / SvelteKit framework route files — loaded by the
    # framework at runtime, never imported via module imports.
    "*/page.tsx",
    "*/page.ts",
    "*/page.jsx",
    "*/page.js",
    "*/layout.tsx",
    "*/layout.ts",
    "*/route.tsx",
    "*/route.ts",
    "*/loading.tsx",
    "*/error.tsx",
    "*/not-found.tsx",
    "*/template.tsx",
    "*/default.tsx",
    # Nuxt route pages
    "*/pages/*.vue",
)

# Decorator patterns that indicate framework usage (route handlers, fixtures, etc.)
_FRAMEWORK_DECORATORS: tuple[str, ...] = (
    "pytest.fixture",
    "pytest.mark",
    # Flask
    "app.route",
    "blueprint.route",
    "bp.route",
    # FastAPI
    "router.get",
    "router.post",
    "router.put",
    "router.delete",
    "router.patch",
    "app.get",
    "app.post",
    # Django
    "admin.register",
    "receiver",
)

# Default dynamic patterns (plugins, handlers, etc.)
_DEFAULT_DYNAMIC_PATTERNS: tuple[str, ...] = (
    "*Plugin",
    "*Handler",
    "*Adapter",
    "*Middleware",
    "*Mixin",
    "*Command",
    "register_*",
    "on_*",
    # Common route/view patterns
    "*_view",
    "*_endpoint",
    "*_route",
    "*_callback",
    "*_signal",
    "*_task",
)

# Path segments that indicate test fixture / sample data directories.
_FIXTURE_PATH_SEGMENTS: tuple[str, ...] = (
    "fixture",
    "fixtures",
    "testdata",
    "test_data",
    "sample_repo",
    "mock_data",
    "test_assets",
)


def _is_fixture_path(path: str) -> bool:
    """Return True if path is under a test fixture / sample data directory."""
    path_lower = path.lower().replace("\\", "/")
    for seg in _FIXTURE_PATH_SEGMENTS:
        if f"/{seg}/" in path_lower or path_lower.startswith(f"{seg}/"):
            return True
    return False

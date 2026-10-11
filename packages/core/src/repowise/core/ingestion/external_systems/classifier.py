"""Heuristic classifier mapping dependency names to C4 categories.

Categories:
    framework — opinionated runtime/HTTP/UI framework (fastapi, next, react,
                django, rails, spring, ...).
    service   — represents an external SaaS / cloud / infra dependency the
                app talks to over the network (stripe, aws-sdk, openai,
                supabase, sentry, ...).
    tool      — build/test/lint/typecheck tooling that wouldn't usually
                appear in an architecture diagram for the running system
                (eslint, vitest, ruff, pytest, mypy, ...).
    library   — default catch-all.

The dictionary is intentionally small and conservative — we'd rather
mis-classify as ``library`` than as ``service`` (a wrong "service" causes
fake boundaries in the C4 diagram). Extend it as we see real repos.
"""

from __future__ import annotations

import re

_FRAMEWORK_NAMES: frozenset[str] = frozenset({
    "fastapi", "django", "flask", "starlette", "tornado", "bottle",
    "next", "nextjs", "react", "vue", "svelte", "nuxt", "remix",
    "astro", "solid-js", "preact", "ember",
    "express", "koa", "hapi", "nestjs", "@nestjs/core",
    "rails", "sinatra", "spring", "spring-boot",
    "rocket", "actix-web", "axum", "warp",
    "gin", "echo", "fiber", "chi",
    "aspnetcore",
})

_SERVICE_NAMES: frozenset[str] = frozenset({
    "stripe", "twilio", "sendgrid", "mailgun", "postmark",
    "openai", "anthropic", "cohere", "together",
    "supabase", "@supabase/supabase-js", "firebase", "firebase-admin",
    "redis", "ioredis", "pymongo", "psycopg2", "psycopg", "asyncpg",
    "sqlalchemy", "prisma", "@prisma/client", "drizzle-orm", "typeorm",
    "sentry-sdk", "@sentry/node", "@sentry/nextjs", "posthog-js", "posthog-node",
    "modal",
})

_SERVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^@aws-sdk/"),
    re.compile(r"^boto3?$"),
    re.compile(r"^@azure/"),
    re.compile(r"^@google-cloud/"),
    re.compile(r"^google-cloud-"),
)

_TOOL_NAMES: frozenset[str] = frozenset({
    "eslint", "prettier", "typescript", "ts-node", "tsx",
    "vitest", "jest", "mocha", "chai", "cypress", "playwright",
    "webpack", "vite", "rollup", "esbuild", "parcel", "turbopack",
    "ruff", "black", "mypy", "pyright", "pytest", "pytest-asyncio",
    "tox", "nox", "coverage", "pylint", "flake8", "isort",
    "alembic",
})


def classify(name: str) -> str:
    """Return one of: ``framework``, ``service``, ``tool``, ``library``."""
    key = name.lower()
    if key in _FRAMEWORK_NAMES:
        return "framework"
    if key in _SERVICE_NAMES:
        return "service"
    for pat in _SERVICE_PATTERNS:
        if pat.match(key):
            return "service"
    if key in _TOOL_NAMES:
        return "tool"
    return "library"


def display_name_for(name: str) -> str:
    """Best-effort prettifier: strip scope, replace separators, title-case."""
    base = name.split("/")[-1] if name.startswith("@") else name
    base = base.replace("-", " ").replace("_", " ")
    return base.title()

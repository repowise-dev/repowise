"""The API server, web UI and their access: env vars read at startup."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Env, Setting

AREA = "server"
_s = factory(AREA)
_e = factory(AREA, scope="env")

SETTINGS: tuple[Setting, ...] = (
    _s(
        "managed_checkout",
        "bool",
        False,
        "This checkout is a clone the server owns: before each server sync it "
        "fetches the tracked branch and fast-forwards to it. Leave off for a "
        "working tree you edit.",
    ),
    _e(
        "REPOWISE_API_KEY",
        "str",
        None,
        "Shared secret that protects the API and the MCP HTTP server; needed for "
        "requests from other machines.",
    ),
    _e(
        "REPOWISE_HOST",
        "str",
        "127.0.0.1",
        "Address the servers listen on. 0.0.0.0 without an API key prints a warning.",
    ),
    _e("REPOWISE_PORT", "int", 7337, "Port for the API server.", minimum=1, maximum=65535),
    _e(
        "REPOWISE_API_URL",
        "str",
        "http://localhost:7337",
        "Where the web UI finds the API. repowise serve sets it.",
    ),
    _e(
        "REPOWISE_UI_PORT",
        "int",
        None,
        "Port the web UI runs on, so the API accepts its setup writes. repowise serve sets it.",
        internal=True,
    ),
    _e("REPOWISE_NO_OPEN", "bool", False, "Do not open a browser when repowise serve starts."),
    _e(
        "REPOWISE_DB_URL",
        "str",
        None,
        "Database to use instead of the repo's SQLite file, for example Postgres.",
    ),
    _e(
        "REPOWISE_DATABASE_URL",
        "str",
        None,
        "Older name for REPOWISE_DB_URL, read when that is unset.",
    ),
    _e(
        "REPOWISE_POSTGRES_KEEPALIVE",
        "bool",
        False,
        "Send server-side TCP keepalive settings and a command timeout on asyncpg "
        "connections. Leave off behind a pooler that rejects startup parameters.",
    ),
    _e(
        "REPOWISE_POSTGRES_KEEPALIVES_IDLE",
        "int",
        60,
        "Seconds idle before the server's first keepalive probe, when "
        "REPOWISE_POSTGRES_KEEPALIVE is on.",
        minimum=1,
        on_invalid="error",
    ),
    _e(
        "REPOWISE_POSTGRES_KEEPALIVES_INTERVAL",
        "int",
        15,
        "Seconds between keepalive probes, when REPOWISE_POSTGRES_KEEPALIVE is on.",
        minimum=1,
        on_invalid="error",
    ),
    _e(
        "REPOWISE_POSTGRES_KEEPALIVES_COUNT",
        "int",
        4,
        "Unanswered keepalive probes before the server drops the connection, when "
        "REPOWISE_POSTGRES_KEEPALIVE is on.",
        minimum=1,
        on_invalid="error",
    ),
    _e(
        "REPOWISE_POSTGRES_COMMAND_TIMEOUT",
        "int",
        300,
        "Seconds one database command may run, when REPOWISE_POSTGRES_KEEPALIVE is on.",
        minimum=1,
        on_invalid="error",
    ),
    _e(
        "REPOWISE_CORS_ORIGINS",
        "list",
        None,
        "Comma-separated web origins allowed to call the API from a browser. Unset "
        "allows any origin without credentials.",
    ),
    _e(
        "REPOWISE_CORS_ALLOW_CREDENTIALS",
        "bool",
        False,
        "Older setting; only logs that any-origin with credentials is refused.",
        env_true=frozenset({"1", "true", "yes"}),
        internal=True,
    ),
    _e(
        "REPOWISE_PUBLIC_URL",
        "str",
        "",
        "Public https address of the MCP server behind a proxy.",
    ),
    _e(
        "REPOWISE_TOKENS_FILE",
        "str",
        "~/.repowise/tokens.yaml",
        "File of scoped access tokens for the server.",
    ),
    _e("REPOWISE_GITHUB_WEBHOOK_SECRET", "str", "", "Secret that verifies GitHub webhooks."),
    _e("REPOWISE_GITLAB_WEBHOOK_TOKEN", "str", "", "Token that verifies GitLab webhooks."),
    _e("REPOWISE_GITEA_WEBHOOK_SECRET", "str", "", "Secret that verifies Gitea webhooks."),
    _e(
        "REPOWISE_PLUGINS",
        "list",
        [],
        "Comma-separated extra plugin modules to load (module or module:attr).",
    ),
    _e(
        "REPOWISE_SEAM_<NAME>",
        "str",
        None,
        "Pick which registered plugin implements an extension point "
        "(REPOWISE_SEAM_AUTHENTICATOR and the like).",
        env=Env("REPOWISE_SEAM_", prefix=True),
        internal=True,
    ),
)

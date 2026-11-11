"""The API server, web UI and their access: env vars read at startup."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Env, Setting

AREA = "server"
_s = factory(AREA)

SETTINGS: tuple[Setting, ...] = (
    _s(
        "REPOWISE_API_KEY",
        "str",
        None,
        "Shared secret that protects the API and the MCP HTTP server; needed for "
        "requests from other machines.",
    ),
    _s(
        "REPOWISE_HOST",
        "str",
        "127.0.0.1",
        "Address the servers listen on. 0.0.0.0 without an API key prints a warning.",
    ),
    _s("REPOWISE_PORT", "int", 7337, "Port for the API server.", minimum=1, maximum=65535),
    _s(
        "REPOWISE_API_URL",
        "str",
        "http://localhost:7337",
        "Where the web UI finds the API. repowise serve sets it.",
    ),
    _s(
        "REPOWISE_UI_PORT",
        "int",
        None,
        "Port the web UI runs on, so the API accepts its setup writes. repowise serve sets it.",
        internal=True,
    ),
    _s("REPOWISE_NO_OPEN", "bool", False, "Do not open a browser when repowise serve starts."),
    _s(
        "REPOWISE_DB_URL",
        "str",
        None,
        "Database to use instead of the repo's SQLite file, for example Postgres.",
    ),
    _s(
        "REPOWISE_DATABASE_URL",
        "str",
        None,
        "Older name for REPOWISE_DB_URL, read when that is unset.",
    ),
    _s(
        "REPOWISE_CORS_ORIGINS",
        "list",
        None,
        "Comma-separated web origins allowed to call the API from a browser. Unset "
        "allows any origin without credentials.",
    ),
    _s(
        "REPOWISE_CORS_ALLOW_CREDENTIALS",
        "bool",
        False,
        "Older setting; only logs that any-origin with credentials is refused.",
        env_true=frozenset({"1", "true", "yes"}),
        internal=True,
    ),
    _s(
        "REPOWISE_PUBLIC_URL",
        "str",
        "",
        "Public https address of the MCP server behind a proxy.",
    ),
    _s(
        "REPOWISE_TOKENS_FILE",
        "str",
        "~/.repowise/tokens.yaml",
        "File of scoped access tokens for the server.",
    ),
    _s("REPOWISE_GITHUB_WEBHOOK_SECRET", "str", "", "Secret that verifies GitHub webhooks."),
    _s("REPOWISE_GITLAB_WEBHOOK_TOKEN", "str", "", "Token that verifies GitLab webhooks."),
    _s("REPOWISE_GITEA_WEBHOOK_SECRET", "str", "", "Secret that verifies Gitea webhooks."),
    _s(
        "REPOWISE_PLUGINS",
        "list",
        [],
        "Comma-separated extra plugin modules to load (module or module:attr).",
    ),
    _s(
        "REPOWISE_SEAM_<NAME>",
        "str",
        None,
        "Pick which registered plugin implements an extension point "
        "(REPOWISE_SEAM_AUTHENTICATOR and the like).",
        env=Env("REPOWISE_SEAM_", prefix=True),
        internal=True,
    ),
)

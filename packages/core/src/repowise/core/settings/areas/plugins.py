"""``plugins:`` in ``.repowise/config.yaml``: settings that plugins declare.

An installed plugin adds its own from ``register()`` with
:func:`repowise.core.settings.registry.register_settings`, built by
:func:`repowise.core.settings.plugins.plugin_setting`, so they exist only
while that plugin is installed and loaded.

Declared here: the built-in ``forge`` issue source
(:mod:`repowise.core.issues.forge`), which ships with repowise and so is
always listed. It runs only once its token is set.
"""

from __future__ import annotations

from repowise.core.settings.plugins import plugin_setting

AREA = "plugins"

FORGE_TOKEN = plugin_setting(
    "forge",
    "token",
    "str",
    None,
    "GitHub or GitLab token that can read this repository's pull or merge requests "
    "and issues. Setting it turns on the built-in forge issue source.",
    secret=True,
)
FORGE_API_URL = plugin_setting(
    "forge",
    "api_url",
    "str",
    "",
    "API endpoint of a self-managed host: GitHub's GraphQL URL "
    "(https://ghe.example.com/api/graphql) or GitLab's API root "
    "(https://git.example.com/api/v4). Empty reads it from the remote.",
)
FORGE_BUG_LABELS = plugin_setting(
    "forge",
    "bug_labels",
    "list",
    ["bug", "type: bug", "type/bug", "type::bug", "kind/bug", "incident"],
    "Labels that make an issue with no issue type a bug (incident for incident), "
    "matched without case. Any other issue is a feature, task or other by its type.",
)

SETTINGS = (FORGE_TOKEN, FORGE_API_URL, FORGE_BUG_LABELS)

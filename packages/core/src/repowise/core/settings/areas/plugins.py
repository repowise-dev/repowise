"""``plugins:`` in ``.repowise/config.yaml``: settings that installed plugins declare.

Nothing is declared here. A plugin adds its own from ``register()`` with
:func:`repowise.core.settings.registry.register_settings`, built by
:func:`repowise.core.settings.plugins.plugin_setting`, so they exist only
while that plugin is installed and loaded.
"""

from __future__ import annotations

AREA = "plugins"

SETTINGS = ()

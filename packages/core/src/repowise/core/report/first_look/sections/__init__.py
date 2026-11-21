"""The built-in first-look sections, one module each.

Every module here is imported, and each registers itself on
``first_look_sections``, so adding a section is adding one file. Modules whose
name starts with ``_`` hold shared helpers and register nothing.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

_log = logging.getLogger("repowise.plugins")

for _module in pkgutil.iter_modules(__path__):
    if _module.name.startswith("_"):
        continue
    try:  # one broken section must not take the others down, as with plugins
        importlib.import_module(f"{__name__}.{_module.name}")
    except Exception as exc:
        _log.warning("repo report section module %s failed to load: %s", _module.name, exc)

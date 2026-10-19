"""The local report sections, one module each.

Every module in this package is imported here, and each registers itself on
``report_sections``, so adding a section is adding one file. A section that
needs data some indexes lack (outcomes, for one) returns ``None`` from
``collect`` when the data is absent and is then left out of the report.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

_log = logging.getLogger("repowise.plugins")

for _module in pkgutil.iter_modules(__path__):
    try:  # one broken section must not take the others down, as with plugins
        importlib.import_module(f"{__name__}.{_module.name}")
    except Exception as exc:
        _log.warning("report section module %s failed to load: %s", _module.name, exc)

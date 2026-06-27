"""Per-language def/use dialects, aggregated into ``DEFUSE_DIALECTS``.

Adding a language's def/use support is two edits and one new module (mirroring
``perf/dialects/__init__.py``):

1. drop ``dataflow/dialects/<lang>.py`` exporting a ``DIALECT`` instance,
2. register it under every ``LanguageTag`` it serves in ``_REGISTER`` below,
3. add ``assignment_kinds`` / ``augmented_assign_kinds`` / ``local_decl_kinds``
   to its ``LanguageNodeMap`` in ``complexity/languages.py``.

No edits to ``defuse.py`` or ``reaching.py``. A language absent here => the
def/use pass is silent for it (no dialect = no signal), the safe default.
"""

from __future__ import annotations

from . import go as _go
from . import python as _python
from . import ts_js as _ts_js
from .base import (
    DEFUSE_DIALECTS,
    BaseDefUseDialect,
    DefUseDialect,
    Occurrence,
    StatementDefUse,
    get_defuse_dialect,
)

# (LanguageTag, dialect instance). One dialect can serve several tags. Each
# key is a ``LanguageTag`` from ``ingestion/models.py``.
_REGISTER: tuple[tuple[str, BaseDefUseDialect], ...] = (
    ("python", _python.DIALECT),
    ("go", _go.DIALECT),
    ("typescript", _ts_js.DIALECT),
    ("tsx", _ts_js.DIALECT),
    ("javascript", _ts_js.DIALECT),
    ("jsx", _ts_js.DIALECT),
)

for _tag, _dialect in _REGISTER:
    DEFUSE_DIALECTS[_tag] = _dialect

__all__ = [
    "DEFUSE_DIALECTS",
    "BaseDefUseDialect",
    "DefUseDialect",
    "Occurrence",
    "StatementDefUse",
    "get_defuse_dialect",
]

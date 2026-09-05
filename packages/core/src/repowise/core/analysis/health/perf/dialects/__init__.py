"""Per-language performance dialects, aggregated into ``PERF_DIALECTS``.

Adding a language's perf support is two edits and one new module (mirroring
``languages/specs/__init__.py``'s ``ALL_SPECS``):

1. drop ``perf/dialects/<lang>.py`` exporting a ``DIALECT`` instance,
2. register it under every ``LanguageTag`` it serves in ``_REGISTER`` below,
3. add ``call_kinds`` (and any ``async_function_kinds``) to its
   ``LanguageNodeMap`` in ``complexity/languages.py``.

No edits to the walker. A language absent here ⇒ the perf pass is silent for it
(no dialect = no signal), which is the safe default.
"""

from __future__ import annotations

from . import cpp as _cpp
from . import csharp as _csharp
from . import dart as _dart
from . import go as _go
from . import java as _java
from . import kotlin as _kotlin
from . import python as _python
from . import ruby as _ruby
from . import rust as _rust
from . import scala as _scala
from . import ts_js as _ts_js
from .base import PERF_DIALECTS, BasePerfDialect

# (LanguageTag, dialect instance). One dialect can serve several tags (TS/JS
# share a grammar). Each entry's key is a ``LanguageTag`` from
# ``ingestion/models.py``.
_REGISTER: tuple[tuple[str, BasePerfDialect], ...] = (
    ("python", _python.DIALECT),
    ("typescript", _ts_js.DIALECT),
    ("tsx", _ts_js.DIALECT),
    # SFC <script> blocks are TS/JS and reach the pass as a TS buffer.
    ("svelte", _ts_js.DIALECT),
    ("vue", _ts_js.DIALECT),
    ("javascript", _ts_js.DIALECT),
    ("jsx", _ts_js.DIALECT),
    ("java", _java.DIALECT),
    ("go", _go.DIALECT),
    ("csharp", _csharp.DIALECT),
    # Razor/Blazor reaches the pass as a C# buffer (its C# regions
    # projected by sfc_source), so the C# dialect applies verbatim.
    ("razor", _csharp.DIALECT),
    ("rust", _rust.DIALECT),
    ("dart", _dart.DIALECT),
    ("scala", _scala.DIALECT),
    ("ruby", _ruby.DIALECT),
    ("kotlin", _kotlin.DIALECT),
    ("cpp", _cpp.DIALECT),
    # NB: "c" shares the C++ grammar but has no ``LanguageNodeMap`` at all
    # (``get_language_map("c")`` is ``None``), so the health pass never reaches
    # a dialect for it. Registering it here would be dead configuration.
)

for _tag, _dialect in _REGISTER:
    PERF_DIALECTS[_tag] = _dialect

__all__ = ["PERF_DIALECTS", "BasePerfDialect"]

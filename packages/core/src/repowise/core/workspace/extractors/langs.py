"""Named file-extension sets, resolved once from the language registry.

Dialects declare the languages they target by importing these constants instead
of re-deriving extension lists, so the language → extension mapping stays in the
registry and a dialect file reads as "this recogniser is for Rust" at a glance.
"""

from __future__ import annotations

from repowise.core.ingestion.languages.registry import REGISTRY as _REGISTRY

JS_TS = _REGISTRY.extensions_for(["javascript", "typescript"])
PYTHON = _REGISTRY.extensions_for(["python"])
JAVA = _REGISTRY.extensions_for(["java"])
PHP = _REGISTRY.extensions_for(["php"])
GO = _REGISTRY.extensions_for(["go"])
CSHARP = _REGISTRY.extensions_for(["csharp"])
RUST = _REGISTRY.extensions_for(["rust"])
PROTO = _REGISTRY.extensions_for(["proto"])

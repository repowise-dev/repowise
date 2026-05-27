"""Language specification registry data — one module per language.

Each ``specs/<tag>.py`` defines a single ``SPEC`` (a
:class:`~repowise.core.ingestion.languages.spec.LanguageSpec`). This package
aggregates them into ``ALL_SPECS`` in a stable, deliberate order: the
extension-to-language map is built first-spec-wins, so order is significant
(e.g. TypeScript before JavaScript). Adding a language = drop a new
``specs/<tag>.py`` module and slot it into ``ALL_SPECS`` below.
"""

from __future__ import annotations

from ..spec import LanguageSpec
from .c import SPEC as _C
from .clojure import SPEC as _CLOJURE
from .cpp import SPEC as _CPP
from .crystal import SPEC as _CRYSTAL
from .csharp import SPEC as _CSHARP
from .dart import SPEC as _DART
from .dlang import SPEC as _DLANG
from .dockerfile import SPEC as _DOCKERFILE
from .elixir import SPEC as _ELIXIR
from .elm import SPEC as _ELM
from .erlang import SPEC as _ERLANG
from .fsharp import SPEC as _FSHARP
from .go import SPEC as _GO
from .graphql import SPEC as _GRAPHQL
from .haskell import SPEC as _HASKELL
from .java import SPEC as _JAVA
from .javascript import SPEC as _JAVASCRIPT
from .json import SPEC as _JSON
from .julia import SPEC as _JULIA
from .kotlin import SPEC as _KOTLIN
from .luau import SPEC as _LUAU
from .makefile import SPEC as _MAKEFILE
from .markdown import SPEC as _MARKDOWN
from .nim import SPEC as _NIM
from .objectivec import SPEC as _OBJECTIVEC
from .ocaml import SPEC as _OCAML
from .openapi import SPEC as _OPENAPI
from .php import SPEC as _PHP
from .proto import SPEC as _PROTO
from .python import SPEC as _PYTHON
from .r import SPEC as _R
from .ruby import SPEC as _RUBY
from .rust import SPEC as _RUST
from .scala import SPEC as _SCALA
from .shell import SPEC as _SHELL
from .sql import SPEC as _SQL
from .swift import SPEC as _SWIFT
from .terraform import SPEC as _TERRAFORM
from .toml import SPEC as _TOML
from .typescript import SPEC as _TYPESCRIPT
from .unknown import SPEC as _UNKNOWN
from .xaml import SPEC as _XAML
from .yaml import SPEC as _YAML
from .zig import SPEC as _ZIG

# Order matters: the registry builds its extension map first-spec-wins.
ALL_SPECS: tuple[LanguageSpec, ...] = (
    # -----------------------------------------------------------------
    # Full-tier languages (AST + imports + calls + heritage + bindings)
    # -----------------------------------------------------------------
    _PYTHON,
    _TYPESCRIPT,
    _JAVASCRIPT,
    _GO,
    _RUST,
    _JAVA,
    # -----------------------------------------------------------------
    # Partial-tier languages (AST + some imports, gaps in calls/bindings)
    # -----------------------------------------------------------------
    _CPP,
    _C,
    # -----------------------------------------------------------------
    # Traversal-tier languages (scaffolded — grammar not yet wired)
    # -----------------------------------------------------------------
    _KOTLIN,
    _RUBY,
    _CSHARP,
    _PHP,
    _SWIFT,
    _SCALA,
    # -----------------------------------------------------------------
    # Config / data / markup languages (passthrough — no AST)
    # -----------------------------------------------------------------
    _SHELL,
    _YAML,
    _JSON,
    _TOML,
    _PROTO,
    _GRAPHQL,
    _TERRAFORM,
    _DOCKERFILE,
    _MAKEFILE,
    _MARKDOWN,
    _SQL,
    _OPENAPI,
    # XAML / AXAML markup for WPF, WinUI 3, UWP, MAUI, Avalonia, Uno.
    # No AST grammar — handled by the XamlDynamicHints extractor which
    # emits ``dynamic_uses`` edges to bound C# types. Registered here so
    # that the traverser surfaces a file node these edges can attach to.
    _XAML,
    # -----------------------------------------------------------------
    # Extra languages — git blame coverage only (passthrough + is_code)
    # These exist so git_indexer tracks their history even though
    # tree-sitter grammars are not installed.
    # -----------------------------------------------------------------
    _OBJECTIVEC,
    _ELIXIR,
    _ERLANG,
    _LUAU,
    _R,
    _DART,
    _ZIG,
    _JULIA,
    _CLOJURE,
    _ELM,
    _HASKELL,
    _OCAML,
    _FSHARP,
    _CRYSTAL,
    _NIM,
    _DLANG,
    # Sentinel for unclassified files
    _UNKNOWN,
)

__all__ = ["ALL_SPECS"]

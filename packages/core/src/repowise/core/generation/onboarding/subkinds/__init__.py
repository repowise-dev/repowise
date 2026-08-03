"""Onboarding subkind modules.

Importing this package triggers registration of each known subkind into the
shared registry (see ``onboarding.registry``). Subkinds register themselves
at import time via a module-level ``register(...)`` call.

The import order here is the canonical declaration order; the registry
re-orders to ``ONBOARDING_ORDER`` for iteration.
"""

# Side-effect imports — each module calls register() at import time.
from . import (
    active_landscape,  # noqa: F401
    codebase_map,  # noqa: F401
    development_guide,  # noqa: F401
    getting_started,  # noqa: F401
    glossary,  # noqa: F401
    guided_tour,  # noqa: F401
    how_it_works,  # noqa: F401
    key_concepts,  # noqa: F401
)

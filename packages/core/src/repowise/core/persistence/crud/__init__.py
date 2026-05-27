"""Async CRUD operations for the repowise persistence layer.

All functions accept an AsyncSession as the first argument; the caller owns
transaction boundaries.  Functions that complete a logical unit of work call
``await session.flush()`` to write changes to the transaction buffer — the
caller must ``await session.commit()`` (or use the ``get_session`` context
manager from database.py).

Versioning contract for upsert_page:
    First upsert  → inserts Page (version=1).  No PageVersion created.
    Second upsert → archives existing Page as a PageVersion, then updates Page
                    in place (version increments).  created_at is preserved.

This module is a façade: the implementations live in per-domain submodules
(repository, pages, graph, external_systems, git, analysis, decisions, chat,
knowledge_graph). Import names from here — sub-module layout may change.
"""

from __future__ import annotations

from .analysis import *  # noqa: F403
from .chat import *  # noqa: F403
from .decisions import *  # noqa: F403
from .external_systems import *  # noqa: F403
from .git import *  # noqa: F403
from .graph import *  # noqa: F403
from .knowledge_graph import *  # noqa: F403
from .pages import *  # noqa: F403
from .repository import *  # noqa: F403

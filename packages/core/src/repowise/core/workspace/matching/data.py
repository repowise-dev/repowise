"""Data matching: a service that models another service's table shares it.

The exact pass links a table's readers (SQL, query builders) to the services
declaring it. A service that only *models* a table (an ORM class, no migration)
while another service defines its schema reads that service's table all the
same, and no consumer row says so. The pass here links them.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .common import internal

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from .common import MatchState

# How strongly a declaration claims a table: creating its schema, evolving it,
# or only modelling it.
_SCHEMA_RANK = {"create": 0, "alter": 1}
_MODEL_RANK = 2


def _rank(c: Contract) -> tuple[int, str, int]:
    return (_SCHEMA_RANK.get(c.meta.get("schema", ""), _MODEL_RANK), c.file_path, c.line or 0)


def shared_table_pass(state: MatchState) -> None:
    """Link each service that only models a table to the one defining its schema.

    Only when exactly one service defines it (a migration or DDL creating or
    altering it): two services that each migrate ``users`` are far more often
    two apps with their own databases than one shared table, and two services
    that only model a table say nothing about whose it is. Each modelling
    service is linked once, from its first model to the owner's strongest
    declaration.
    """
    for key, providers in state.provider_index.items():
        if not key.startswith("data::"):
            continue
        by_service: dict[tuple[str, str | None], list[Contract]] = defaultdict(list)
        for p in providers:
            by_service[(p.repo, p.service)].append(p)
        if len(by_service) < 2:
            continue
        best = [min(group, key=_rank) for group in by_service.values()]
        owners = [c for c in best if _rank(c)[0] < _MODEL_RANK]
        if len(owners) != 1:
            continue
        owner = owners[0]
        for other in best:
            if other is not owner and not internal(owner, other):
                state.add(other, owner, "exact", min(owner.confidence, other.confidence))

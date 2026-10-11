"""The shared code-health ``APIRouter`` instance.

Leaf module so every route submodule can ``from ._router import router`` and
attach its handlers to the one router without importing (and re-entering) the
package ``__init__``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from repowise.server.deps import verify_api_key

router = APIRouter(
    tags=["code-health"],
    dependencies=[Depends(verify_api_key)],
)

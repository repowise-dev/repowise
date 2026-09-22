"""Python socket endpoints: FastAPI websocket routes."""

from __future__ import annotations

import re

from ..langs import PYTHON
from .dialect import SocketDialect, SocketPattern

PYTHON_SOCKETS = SocketDialect(
    name="python-sockets",
    extensions=PYTHON,
    patterns=(
        SocketPattern(
            regex=re.compile(
                r"""@(?:app|router)\.websocket\s*\(\s*['"]([^'"]+)['"]""", re.IGNORECASE
            ),
            role="provider",
            transport="fastapi-websocket",
            confidence=0.8,
            label="@app.websocket",
            identity_group=1,
        ),
    ),
)

__all__ = ["PYTHON_SOCKETS"]

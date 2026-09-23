"""Python socket endpoints: FastAPI websocket routes."""

from __future__ import annotations

import re

from ..langs import PYTHON
from ..strings import Arg
from .dialect import SocketCall, SocketDialect

PYTHON_SOCKETS = SocketDialect(
    name="python-sockets",
    calls=(
        SocketCall(
            head=re.compile(r"@(?:app|router)\.websocket\s*\(", re.IGNORECASE),
            role="provider",
            transport="fastapi-websocket",
            label="@app.websocket",
            extensions=PYTHON,
            name=Arg(keys=("path",), pos=0),
        ),
    ),
)

__all__ = ["PYTHON_SOCKETS"]

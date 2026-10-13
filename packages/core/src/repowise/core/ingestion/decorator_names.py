"""The base name of one decorator or annotation token, shared by every reader."""

from __future__ import annotations


def decorator_base(raw: str) -> str:
    """``@app.route("/x")`` -> ``app.route``; ``"Deprecated\n public"`` -> ``Deprecated``.

    Strips a leading ``@`` and any call-argument tail. Without a tail only the
    first word is kept, so visibility keywords trailing a Java/Kotlin modifier
    blob fragment are dropped. Handles ONE token: callers holding a
    multi-annotation blob split on ``@`` first.
    """
    base = raw.lstrip("@").strip()
    paren = base.find("(")
    if paren >= 0:
        return base[:paren].strip()
    parts = base.split()
    return parts[0] if parts else base

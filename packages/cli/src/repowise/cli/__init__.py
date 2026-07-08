"""repowise CLI package.

Entry point: ``repowise`` command (defined in repowise.cli.main).
Codebase intelligence for developers and AI — dependency graphs,
git signals, dead code detection, architectural decisions, and
AI-generated documentation.
"""

from repowise.cli._stdio import _ensure_utf8_stdio

# Reconfigure stdout/stderr to UTF-8 with ``errors="replace"`` before any
# Rich Console is constructed downstream. Without this, Windows shells
# defaulting to cp1252 crash ``repowise init`` (and any other Rich-driven
# command) mid-pipeline when a non-ASCII glyph like ``↳`` or ``✓`` is
# printed — see issue #271. Safe and silent on Linux/macOS where stdio
# is already UTF-8.
_ensure_utf8_stdio()

__version__ = "0.28.1"

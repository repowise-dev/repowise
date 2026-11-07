"""Re-export: the detached spawn lives in core so pipeline code can use it too."""

from repowise.core.spawn import spawn_detached

__all__ = ["spawn_detached"]

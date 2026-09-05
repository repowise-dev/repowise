"""Per-tool callbacks the shared response budget runs, without naming a tool.

The budgeter owns accounting, tier selection, shedding, and omission storage
for every tool. A few tools need one more step once shedding has decided what
survives: a claim re-derived from the lanes that are left, a cross-reference
pruned to the rows still emitted. Those steps know field names the shared layer
must not, so a tool registers them here at import time instead of the budgeter
importing back into a tool module.

``post_shed`` runs after shedding while the collector is still open, so a hook
may route what it drops into recovery. Only the ``blocks`` strategy has that
window; the ``targets`` strategy attaches inside :func:`truncate_to_budget`.

``post_enforce`` runs on the settled shape, after the final size check, for
claims about what was delivered.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repowise.server.mcp_server._budget.collector import OmissionCollector

PostShedHook = Callable[[dict[str, Any], "OmissionCollector"], None]
PostEnforceHook = Callable[[dict[str, Any]], None]

_POST_SHED: dict[str, list[PostShedHook]] = {}
_POST_ENFORCE: dict[str, list[PostEnforceHook]] = {}


def register_post_shed(tool: str, hook: PostShedHook) -> None:
    """Register *hook* to run for *tool* while the omission collector is open."""
    _register(_POST_SHED, tool, hook)


def register_post_enforce(tool: str, hook: PostEnforceHook) -> None:
    """Register *hook* to run for *tool* once the delivered shape is settled."""
    _register(_POST_ENFORCE, tool, hook)


def _register(registry: dict[str, list[Any]], tool: str, hook: Any) -> None:
    # Idempotent: a tool module re-imported under a test reload would otherwise
    # stack duplicate hooks onto every later response.
    hooks = registry.setdefault(tool, [])
    if hook not in hooks:
        hooks.append(hook)


def run_post_shed(tool: str, result: dict[str, Any], collector: OmissionCollector) -> None:
    """Run *tool*'s post-shed hooks, in registration order."""
    for hook in _POST_SHED.get(tool, ()):
        hook(result, collector)


def run_post_enforce(tool: str, result: dict[str, Any]) -> None:
    """Run *tool*'s post-enforce hooks, in registration order."""
    for hook in _POST_ENFORCE.get(tool, ()):
        hook(result)


def registered_hook_tools() -> frozenset[str]:
    """Tools with at least one hook, for coverage assertions."""
    return frozenset(_POST_SHED) | frozenset(_POST_ENFORCE)


__all__ = [
    "PostEnforceHook",
    "PostShedHook",
    "register_post_enforce",
    "register_post_shed",
    "registered_hook_tools",
    "run_post_enforce",
    "run_post_shed",
]

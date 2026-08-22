"""Compatibility imports for the shared execution-graph index.

The implementation lives outside the performance package so performance and
test intelligence cannot drift on edge policy or graph construction.
"""

from repowise.core.analysis.execution_graph import ExecutionGraphIndex, module_node_id

CallGraphIndex = ExecutionGraphIndex

__all__ = ["CallGraphIndex", "ExecutionGraphIndex", "module_node_id"]

"""Object Pascal (Delphi/FreePascal) ``PerfDialect``.

Pascal-specific performance markers and callee extraction.
Object Pascal uses ``exprCall``, bare ``statement``, and ``assignment`` nodes for calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node


class PascalPerfDialect(BasePerfDialect):
    language = "pascal"
    markers = frozenset()

    # In Pascal, calls can be expressions, bare statements, or assignments.
    # The member access is usually via `exprDot`.
    attribute_callee_kinds = frozenset({"exprDot"})

    def callee_root_name(self, call_node: Node) -> str | None:
        """Root identifier of a call's callee."""
        # A bare statement or assignment might be a parenless call.
        if call_node.type == "statement":
            # The identifier or exprDot is usually the only named child.
            named = [c for c in call_node.children if c.is_named]
            if named:
                return super().callee_root_name(named[0])
            return None
        
        if call_node.type == "assignment":
            # For `Result := GetDefaultNDNProfile;`, `GetDefaultNDNProfile` is on the RHS.
            rhs = call_node.child_by_field_name("rhs")
            if rhs is not None:
                return super().callee_root_name(rhs)
            return None
            
        # For exprCall, the callable is usually the first named child or labeled 'entity'/'lhs'
        entity = call_node.child_by_field_name("entity")
        if entity is not None:
            return super().callee_root_name(entity)
            
        return super().callee_root_name(call_node)

    def callee_method_name(self, call_node: Node) -> str | None:
        """Rightmost member of the callee."""
        if call_node.type == "statement":
            named = [c for c in call_node.children if c.is_named]
            if named:
                return super().callee_method_name(named[0])
            return None
            
        if call_node.type == "assignment":
            rhs = call_node.child_by_field_name("rhs")
            if rhs is not None:
                return super().callee_method_name(rhs)
            return None

        entity = call_node.child_by_field_name("entity")
        if entity is not None:
            return super().callee_method_name(entity)
            
        return super().callee_method_name(call_node)

    def callee_is_attribute(self, call_node: Node) -> bool:
        if call_node.type == "statement":
            named = [c for c in call_node.children if c.is_named]
            if named:
                return super().callee_is_attribute(named[0])
            return False
            
        if call_node.type == "assignment":
            rhs = call_node.child_by_field_name("rhs")
            if rhs is not None:
                return super().callee_is_attribute(rhs)
            return False

        entity = call_node.child_by_field_name("entity")
        if entity is not None:
            return super().callee_is_attribute(entity)
            
        return super().callee_is_attribute(call_node)


DIALECT = PascalPerfDialect()

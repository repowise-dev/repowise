"""The unused-internal pass: private symbols no call, reference or override reaches.

COBOL paragraphs are judged from the program's source text instead, by
``cobol_paragraphs.py``.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from repowise.core.ingestion.symbol_identity import make_symbol_id

from ..apex_reachability import is_apex_entry_member
from ..cobol_paragraphs import unreachable_paragraph_findings
from ..constants import _DEAD_CODE_EXEMPT_LANGUAGES, _is_fixture_path
from ..contract_methods import is_contract_method
from ..decorator_rules import _declared_deliberately_unused, _is_framework_registered
from ..finding_helpers import _symbol_span
from ..models import DeadCodeFindingData, DeadCodeKind
from ..risk_factors import path_risk_factors
from ..symbol_rules import (
    _ENTRY_POINT_SYMBOL_NAMES,
    _TYPE_DECLARATION_KINDS,
    _UNCALLABLE_TYPE_KINDS,
    _non_importable_kinds,
)

if TYPE_CHECKING:
    from ..analyzer import DeadCodeAnalyzer

# Rust: the compiler's ``dead_code`` lint already reports these, with macro
# expansion and types a static graph cannot match. The exempt languages
# (COBOL) are entered by a runtime the graph cannot see, as in the file and
# export passes; a COBOL paragraph is judged by ``cobol_paragraphs`` instead.
_UNJUDGED_LANGUAGES = _DEAD_CODE_EXEMPT_LANGUAGES | {"rust"}


class _FileMembers(NamedTuple):
    """The symbols of one file by the name of their parent type, and how many
    types of the file bear each name."""

    by_parent: dict[str, list[str]]
    type_names: Counter[str]


class UnusedInternalPass:
    """Judges every private symbol node of the graph."""

    def __init__(self, ctx: DeadCodeAnalyzer) -> None:
        self.ctx = ctx
        self.graph = ctx.graph
        self._members: dict[str, _FileMembers] = {}

    def run(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect private symbols with zero incoming call edges.

        On by default. These carry a higher false-positive rate than the other
        detectors, which is why they land at a lower confidence. Disable with
        ``detect_unused_internals=False`` in the config dict.
        """
        findings: list[DeadCodeFindingData] = []

        for node, node_data in self.graph.nodes(data=True):
            if not self._is_internal_candidate(node_data, dynamic_patterns, whitelist):
                continue
            if self._is_used(node, node_data):
                continue
            file_path = node_data.get("file_path", "")
            sym_name = node_data.get("name", "")

            # 0.65 even when deprecated: a private symbol has no outside
            # consumer, unlike an export (0.3), so deprecation is no reason to hide it.
            findings.append(
                DeadCodeFindingData(
                    kind=DeadCodeKind.UNUSED_INTERNAL,
                    file_path=file_path,
                    symbol_name=sym_name,
                    symbol_kind=node_data.get("kind"),
                    confidence=0.65,
                    reason=f"Private symbol '{sym_name}' is not used anywhere",
                    evidence=[f"No call, reference or override reaches '{sym_name}'"],
                    safe_to_delete=False,
                    risk_factors=list(path_risk_factors(file_path)),
                    **_symbol_span(node_data),
                    **self.ctx._git_fields(file_path),
                )
            )

        # COBOL's one claim, read from source rather than call edges.
        return findings + unreachable_paragraph_findings(self.ctx, whitelist)

    def _is_used(self, node: str, node_data: dict) -> bool:
        """Whether anything in the graph uses the private symbol *node*."""
        file_path = node_data.get("file_path", "")
        sym_name = node_data.get("name", "")
        # Any inbound use, not only a call (a subclassed base, a handler
        # named as a value). ``new X(..)`` lands on X's constructor
        # (``path::X::X``) when X declares one, and constructing X uses it.
        if self.ctx._has_inbound_use(node) or self.ctx._has_inbound_use(
            make_symbol_id(file_path, sym_name, sym_name)
        ):
            return True
        if node_data.get("kind") in _TYPE_DECLARATION_KINDS and self._used_through_a_member(
            node, file_path, sym_name
        ):
            return True
        # A private helper imported by name elsewhere (a dispatch table
        # entry) is referenced even with no ``calls`` edge. A Pascal
        # ``uses`` wildcard never reaches a unit's private routines: they
        # are not in its interface.
        wildcard_rescues = node_data.get("language") != "pascal"
        return any(
            sym_name in imported or ("*" in imported and wildcard_rescues)
            for imported in self.ctx._imported_names_by_edge(file_path)
        )

    def _used_through_a_member(self, node: str, file_path: str, name: str) -> bool:
        """Whether code outside the type *node* calls a member of it.

        A generated type, such as a Lombok builder, has no constructor call
        and no name in the source for a use of the type itself to land on.
        Only a call counts: an override's dispatch edge is no use of its
        class. A member's id names its parent by simple name alone, so a name
        that two types of the file bear settles nothing.
        """
        members = self._file_members(file_path)
        if members.type_names[name] != 1:
            return False
        return any(
            self.graph.get_edge_data(pred, member, {}).get("edge_type") == "calls"
            and not self.ctx._is_self_use(pred, node)
            for member in members.by_parent.get(name, ())
            for pred in self.graph.predecessors(member)
        )

    def _file_members(self, file_path: str) -> _FileMembers:
        """The members of *file_path*, read once per file."""
        if file_path not in self._members:
            members: dict[str, list[str]] = {}
            type_names: Counter[str] = Counter()
            prefix = file_path + "::"
            successors = self.graph.successors(file_path) if self.graph.has_node(file_path) else ()
            for symbol in successors:
                data = self.graph.nodes[symbol]
                if data.get("kind") in _TYPE_DECLARATION_KINDS:
                    type_names[data.get("name", "")] += 1
                scope = symbol[len(prefix) :].split("::") if symbol.startswith(prefix) else []
                if len(scope) > 1:
                    members.setdefault(scope[-2], []).append(symbol)
            self._members[file_path] = _FileMembers(members, type_names)
        return self._members[file_path]

    def _is_internal_candidate(
        self, node_data: dict, dynamic_patterns: tuple[str, ...], whitelist: set[str]
    ) -> bool:
        """Whether a graph node is a private symbol the unused-internal pass may judge."""
        if node_data.get("node_type") != "symbol":
            return False
        if node_data.get("language") in _UNJUDGED_LANGUAGES:
            return False
        # ``internal`` is assembly-, module- or crate-wide, so a missing call
        # edge is no evidence; no pass judges it, which drops an unmodified
        # C# top-level type out of both symbol passes.
        if node_data.get("visibility") != "private":
            return False
        file_path = node_data.get("file_path", "")
        if not file_path:
            return False
        file_data = self.graph.nodes.get(file_path, {})
        if file_data.get("is_test", False):
            return False
        if _is_fixture_path(file_path):
            return False
        if self.ctx._should_never_flag(file_path, whitelist):
            return False
        return self._is_judgeable_internal(node_data, dynamic_patterns)

    def _is_judgeable_internal(self, node_data: dict, dynamic_patterns: tuple[str, ...]) -> bool:
        """Whether a missing call edge says anything about this private symbol."""
        sym_name = node_data.get("name", "")
        if sym_name.startswith("__") and sym_name.endswith("__"):
            return False
        if sym_name in _ENTRY_POINT_SYMBOL_NAMES:
            return False
        # Namespace-path fragments (e.g. ``eShop.ClientApp``) and
        # non-callable kinds bypass the call-edge pass by design.
        if "." in sym_name:
            return False
        if node_data.get("kind") in _non_importable_kinds(node_data.get("language", "unknown")):
            return False
        # Type kinds have no call edges; the export pass still judges them.
        if node_data.get("kind") in _UNCALLABLE_TYPE_KINDS:
            return False
        if is_contract_method(
            sym_name,
            node_data.get("kind"),
            node_data.get("language", "unknown"),
            modifiers=node_data.get("modifiers"),
        ):
            return False
        if self.ctx._name_matches_dynamic(sym_name, dynamic_patterns):
            return False

        # A private ``@future`` or ``@TestVisible`` Apex member: the platform or a test calls it.
        if is_apex_entry_member(node_data):
            return False
        # A private ``@PostConstruct`` or ``@Scheduled`` method is called by
        # the container, as in the export pass.
        decorators = node_data.get("decorators") or []
        meta = self.ctx._framework_meta_annotations(node_data.get("file_path", ""))
        return not (
            _is_framework_registered(decorators, meta) or _declared_deliberately_unused(decorators)
        )

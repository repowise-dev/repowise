"""WSDL SOAP contract dialect — parses ``.wsdl`` files into IDL provider contracts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from repowise.core.workspace.origins import ContractOrigin

from ..base import ScanContext, line_at
from ..dialect import EVERY_FILE, build_contract
from .common import soap_contract_id

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_OP_LINE_RE = re.compile(r"<\w*:?operation\b[^>]*\bname=[\"'](?P<name>[^\"']+)[\"']")


def _operation_lines(content: str) -> dict[str, int]:
    op_lines: dict[str, int] = {}
    for m in _OP_LINE_RE.finditer(content):
        name = m.group("name")
        if name not in op_lines:
            op_lines[name] = line_at(content, m.start())
    return op_lines


def _element_tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


def _extract_operations(
    ctx: ScanContext,
    root: ET.Element,
    target_ns: str,
    op_lines: dict[str, int],
) -> list[Contract]:
    out: list[Contract] = []
    seen_ops: set[str] = set()
    for elem in root.iter():
        if _element_tag(elem) != "operation":
            continue
        op_name = elem.attrib.get("name")
        if not op_name or op_name in seen_ops:
            continue
        seen_ops.add(op_name)
        c = build_contract(
            ctx,
            contract_type="soap",
            contract_id=soap_contract_id(target_ns, op_name),
            role="provider",
            symbol_name=f"wsdl:{op_name}",
            origin=ContractOrigin.IDL,
            line=op_lines.get(op_name),
            meta={"namespace": target_ns, "operation": op_name},
        )
        out.append(c)
    return out


class WsdlDialect:
    name = "wsdl"
    extensions = frozenset({".wsdl"})
    requires = EVERY_FILE

    def extract(self, ctx: ScanContext) -> list[Contract]:
        try:
            root = ET.fromstring(ctx.content)
        except ET.ParseError:
            return []

        target_ns = root.attrib.get("targetNamespace")
        if not target_ns:
            return []

        op_lines = _operation_lines(ctx.content)
        return _extract_operations(ctx, root, target_ns, op_lines)

"""JAX-WS SOAP contract dialect — parses JAX-WS endpoints and wsimport clients."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..calls import code_matches
from ..dialect import build_contract
from ..langs import JAVA
from .common import soap_contract_id

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_TARGET_NS_RE = re.compile(r"""targetNamespace\s*=\s*["']([^"']+)["']""")
_PACKAGE_RE = re.compile(r"\bpackage\s+([A-Za-z0-9_.]+)\s*;")

_WEB_SERVICE_RE = re.compile(
    r"@WebService\b(?:\s*\((?P<args>[^)]*)\))?\s*(?:public\s+|abstract\s+|final\s+)*(?P<kind>class|interface)\s+(?P<name>\w+)"
)
_WEB_METHOD_RE = re.compile(
    r"@WebMethod\b(?:\s*\((?P<args>[^)]*)\))?\s*(?:public\s+|protected\s+)?(?:static\s+)?[\w<>[\]?,\s]+\s+(?P<name>\w+)\s*\("
)
_INTERFACE_METHOD_RE = re.compile(
    r"^\s*(?:public\s+)?(?:abstract\s+)?[\w<>[\]?,\s]+\s+(?P<name>\w+)\s*\([^)]*\)\s*;",
    re.MULTILINE,
)

_WEB_SERVICE_CLIENT_RE = re.compile(r"@WebServiceClient\b(?:\s*\((?P<args>[^)]*)\))?")
_PORT_VAR_RE = re.compile(
    r"""(?:(\w+Port)\s+(\w+)|(\w+)\s*=\s*[^;\n]*\.get\w*Port\s*\()"""
)
_CHAINED_PORT_CALL_RE = re.compile(
    r"""\.get\w*Port\s*\(\s*\)\s*\.\s*(?P<op>\w+)\s*\("""
)


def _default_namespace(package: str | None) -> str:
    if not package:
        return "http://tempuri.org/"
    parts = package.split(".")
    return f"http://{'.'.join(reversed(parts))}/"


def _extract_web_methods(
    ctx: ScanContext,
    ws_m: re.Match[str],
    namespace: str,
    class_name: str,
) -> tuple[list[Contract], set[str]]:
    out: list[Contract] = []
    seen_ops: set[str] = set()
    for wm in code_matches(ctx, _WEB_METHOD_RE):
        if wm.start() < ws_m.start():
            continue
        op_name = wm.group("name")
        w_args = wm.group("args") or ""
        op_m = re.search(r"""operationName\s*=\s*["']([^"']+)["']""", w_args)
        if op_m:
            op_name = op_m.group(1)
        seen_ops.add(op_name)
        c = build_contract(
            ctx,
            contract_type="soap",
            contract_id=soap_contract_id(namespace, op_name),
            role="provider",
            symbol_name=f"{class_name}.{op_name}",
            origin=ContractOrigin.ROUTE_REGISTRATION,
            line=line_at(ctx.content, wm.start()),
            meta={"namespace": namespace, "operation": op_name},
        )
        out.append(c)
    return out, seen_ops


def _extract_interface_methods(
    ctx: ScanContext,
    ws_m: re.Match[str],
    namespace: str,
    class_name: str,
    seen_ops: set[str],
) -> list[Contract]:
    out: list[Contract] = []
    for im in _INTERFACE_METHOD_RE.finditer(ctx.content):
        if im.start() < ws_m.start() or ctx.in_comment(im.start()):
            continue
        op_name = im.group("name")
        if op_name in seen_ops:
            continue
        seen_ops.add(op_name)
        c = build_contract(
            ctx,
            contract_type="soap",
            contract_id=soap_contract_id(namespace, op_name),
            role="provider",
            symbol_name=f"{class_name}.{op_name}",
            origin=ContractOrigin.ROUTE_REGISTRATION,
            line=line_at(ctx.content, im.start()),
            meta={"namespace": namespace, "operation": op_name},
        )
        out.append(c)
    return out


def _extract_providers(ctx: ScanContext, default_ns: str) -> list[Contract]:
    out: list[Contract] = []
    for ws_m in code_matches(ctx, _WEB_SERVICE_RE):
        args = ws_m.group("args") or ""
        tns_m = _TARGET_NS_RE.search(args)
        namespace = tns_m.group(1) if tns_m else default_ns
        class_name = ws_m.group("name")
        is_interface = ws_m.group("kind") == "interface"

        contracts, seen_ops = _extract_web_methods(ctx, ws_m, namespace, class_name)
        out.extend(contracts)
        if is_interface and not seen_ops:
            out.extend(_extract_interface_methods(ctx, ws_m, namespace, class_name, seen_ops))
    return out


def _client_namespace(ctx: ScanContext, default_ns: str) -> str:
    wsc_m = _WEB_SERVICE_CLIENT_RE.search(ctx.content)
    if wsc_m and not ctx.in_comment(wsc_m.start()):
        args = wsc_m.group("args") or ""
        tns_m = _TARGET_NS_RE.search(args)
        return tns_m.group(1) if tns_m else default_ns
    tns_m = _TARGET_NS_RE.search(ctx.content)
    if tns_m and not ctx.in_comment(tns_m.start()):
        return tns_m.group(1)
    return default_ns


def _collect_port_vars(ctx: ScanContext) -> set[str]:
    port_vars: set[str] = set()
    for m in _PORT_VAR_RE.finditer(ctx.content):
        if not ctx.in_comment(m.start()):
            v = m.group(2) or m.group(3)
            if v:
                port_vars.add(v)
    return port_vars


def _extract_port_variable_calls(
    ctx: ScanContext, port_vars: set[str], namespace: str
) -> list[Contract]:
    out: list[Contract] = []
    for pv in port_vars:
        call_re = re.compile(rf"\b{re.escape(pv)}\s*\.\s*(?P<op>\w+)\s*\(")
        for cm in call_re.finditer(ctx.content):
            if ctx.in_comment(cm.start()):
                continue
            op_name = cm.group("op")
            c = build_contract(
                ctx,
                contract_type="soap",
                contract_id=soap_contract_id(namespace, op_name),
                role="consumer",
                symbol_name=f"{pv}.{op_name}",
                origin=ContractOrigin.CLIENT_CALL,
                line=line_at(ctx.content, cm.start()),
                meta={"namespace": namespace, "operation": op_name},
            )
            out.append(c)
    return out


def _extract_chained_port_calls(ctx: ScanContext, namespace: str) -> list[Contract]:
    out: list[Contract] = []
    for cm in _CHAINED_PORT_CALL_RE.finditer(ctx.content):
        if ctx.in_comment(cm.start()):
            continue
        op_name = cm.group("op")
        c = build_contract(
            ctx,
            contract_type="soap",
            contract_id=soap_contract_id(namespace, op_name),
            role="consumer",
            symbol_name=f"port.{op_name}",
            origin=ContractOrigin.CLIENT_CALL,
            line=line_at(ctx.content, cm.start()),
            meta={"namespace": namespace, "operation": op_name},
        )
        out.append(c)
    return out


def _extract_consumers(ctx: ScanContext, default_ns: str) -> list[Contract]:
    namespace = _client_namespace(ctx, default_ns)
    port_vars = _collect_port_vars(ctx)
    out = _extract_port_variable_calls(ctx, port_vars, namespace)
    out.extend(_extract_chained_port_calls(ctx, namespace))
    return out


class JaxWsDialect:
    name = "jaxws"
    extensions = JAVA
    requires = ("jakarta.jws", "javax.jws", "javax.xml.ws")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        pkg_m = _PACKAGE_RE.search(ctx.content)
        pkg = pkg_m.group(1) if pkg_m else None
        default_ns = _default_namespace(pkg)

        out = _extract_providers(ctx, default_ns)
        out.extend(_extract_consumers(ctx, default_ns))
        return out

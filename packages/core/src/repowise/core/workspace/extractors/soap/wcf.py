"""WCF SOAP contract dialect — parses ServiceContract endpoints and ChannelFactory clients."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..calls import code_matches
from ..dialect import build_contract
from ..langs import CSHARP
from .common import soap_contract_id

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_NAMESPACE_ARG_RE = re.compile(r"""Namespace\s*=\s*["']([^"']+)["']""")

_SERVICE_CONTRACT_RE = re.compile(
    r"\[ServiceContract(?:\((?P<args>[^\]]*)\))?\]\s*(?:public\s+|internal\s+)?(?:interface|class)\s+(?P<name>\w+)"
)
_OPERATION_CONTRACT_RE = re.compile(
    r"\[OperationContract(?:\((?P<args>[^\]]*)\))?\]\s*(?:[A-Za-z0-9_<>[\], ?\s]+)\s+(?P<name>\w+)\s*\("
)

_CHANNEL_FACTORY_RE = re.compile(r"ChannelFactory<(?P<iface>\w+)>")
_FACTORY_DECL_RE = re.compile(
    r"(?:ChannelFactory\s*<\s*(?P<iface1>\w+)\s*>\s+(?P<var1>\w+)|(?P<var2>\w+)\s*=\s*(?:new\s+)?ChannelFactory\s*<\s*(?P<iface2>\w+)\s*>)"
)
_FACTORY_CHANNEL_RE = re.compile(
    r"(?P<var>\w+)\s*=\s*(?P<factory>\w+)\s*\.\s*CreateChannel\s*\("
)
_DIRECT_CHANNEL_RE = re.compile(
    r"(?P<var>\w+)\s*=\s*[^;\n]*ChannelFactory\s*<\s*(?P<iface>\w+)\s*>[^;\n]*\.\s*CreateChannel\s*\("
)
_TYPED_CHANNEL_RE = re.compile(
    r"(?P<iface>[A-Za-z0-9_]+)\s+(?P<var>\w+)\s*=\s*[^;\n]*\.CreateChannel\s*\("
)
_CREATE_CHANNEL_RE = re.compile(
    r"(?P<var>\w+)\s*=\s*[^;\n]*\.CreateChannel\s*\("
)
_CHAINED_CHANNEL_CALL_RE = re.compile(
    r"\.CreateChannel\s*\([^)]*\)\s*\.\s*(?P<op>\w+)\s*\("
)


def _extract_operation_contracts(
    ctx: ScanContext,
    sc_m: re.Match[str],
    ns: str,
    iface_name: str,
) -> list[Contract]:
    out: list[Contract] = []
    for op_m in code_matches(ctx, _OPERATION_CONTRACT_RE):
        if op_m.start() < sc_m.start():
            continue
        op_name = op_m.group("name")
        op_args = op_m.group("args") or ""
        name_m = re.search(r"""Name\s*=\s*["']([^"']+)["']""", op_args)
        if name_m:
            op_name = name_m.group(1)
        c = build_contract(
            ctx,
            contract_type="soap",
            contract_id=soap_contract_id(ns, op_name),
            role="provider",
            symbol_name=f"{iface_name}.{op_name}",
            origin=ContractOrigin.ROUTE_REGISTRATION,
            line=line_at(ctx.content, op_m.start()),
            meta={"namespace": ns, "operation": op_name},
        )
        out.append(c)
    return out


def _extract_providers(ctx: ScanContext) -> tuple[list[Contract], dict[str, str]]:
    out: list[Contract] = []
    contracts_by_iface: dict[str, str] = {}
    for sc_m in code_matches(ctx, _SERVICE_CONTRACT_RE):
        args = sc_m.group("args") or ""
        ns_m = _NAMESPACE_ARG_RE.search(args)
        ns = ns_m.group(1) if ns_m else "http://tempuri.org/"
        iface_name = sc_m.group("name")
        contracts_by_iface[iface_name] = ns
        out.extend(_extract_operation_contracts(ctx, sc_m, ns, iface_name))
    return out, contracts_by_iface


def _resolve_default_ns(ctx: ScanContext, contracts_by_iface: dict[str, str]) -> str:
    if contracts_by_iface:
        return next(iter(contracts_by_iface.values()))
    ns_m = _NAMESPACE_ARG_RE.search(ctx.content)
    if ns_m and not ctx.in_comment(ns_m.start()):
        return ns_m.group(1)
    return "http://tempuri.org/"


def _collect_factory_vars(ctx: ScanContext) -> dict[str, str]:
    factories: dict[str, str] = {}
    for m in _FACTORY_DECL_RE.finditer(ctx.content):
        if ctx.in_comment(m.start()):
            continue
        var = m.group("var1") or m.group("var2")
        iface = m.group("iface1") or m.group("iface2")
        if var and iface:
            factories[var] = iface
    return factories


def _collect_factory_channel_vars(
    ctx: ScanContext,
    factories: dict[str, str],
) -> dict[str, str]:
    channel_vars: dict[str, str] = {}
    for m in _DIRECT_CHANNEL_RE.finditer(ctx.content):
        if not ctx.in_comment(m.start()):
            channel_vars[m.group("var")] = m.group("iface")
    for m in _FACTORY_CHANNEL_RE.finditer(ctx.content):
        if not ctx.in_comment(m.start()):
            var, fac = m.group("var"), m.group("factory")
            if fac in factories:
                channel_vars[var] = factories[fac]
    return channel_vars


def _collect_typed_channels(
    ctx: ScanContext,
    channel_vars: dict[str, str],
) -> None:
    for m in _TYPED_CHANNEL_RE.finditer(ctx.content):
        if not ctx.in_comment(m.start()):
            iface, var = m.group("iface"), m.group("var")
            if iface not in ("var", "dynamic") and var not in channel_vars:
                channel_vars[var] = iface


def _apply_lone_interface_fallback(
    ctx: ScanContext,
    factories: dict[str, str],
    contracts_by_iface: dict[str, str],
    channel_vars: dict[str, str],
) -> None:
    all_fac_ifaces = set(factories.values()) | set(contracts_by_iface.keys())
    fallback_iface = next(iter(all_fac_ifaces)) if len(all_fac_ifaces) == 1 else None
    if fallback_iface:
        for m in _CREATE_CHANNEL_RE.finditer(ctx.content):
            if not ctx.in_comment(m.start()):
                channel_vars.setdefault(m.group("var"), fallback_iface)


def _collect_channel_vars(
    ctx: ScanContext,
    factories: dict[str, str],
    contracts_by_iface: dict[str, str],
) -> dict[str, str]:
    channel_vars = _collect_factory_channel_vars(ctx, factories)
    _collect_typed_channels(ctx, channel_vars)
    _apply_lone_interface_fallback(ctx, factories, contracts_by_iface, channel_vars)
    return channel_vars


def _extract_channel_var_calls(
    ctx: ScanContext,
    iface: str,
    ns: str,
    cv: str,
) -> list[Contract]:
    out: list[Contract] = []
    call_re = re.compile(rf"\b{re.escape(cv)}\s*\.\s*(?P<op>\w+)\s*\(")
    for cm in call_re.finditer(ctx.content):
        if ctx.in_comment(cm.start()):
            continue
        op_name = cm.group("op")
        c = build_contract(
            ctx,
            contract_type="soap",
            contract_id=soap_contract_id(ns, op_name),
            role="consumer",
            symbol_name=f"{iface}.{op_name}",
            origin=ContractOrigin.CLIENT_CALL,
            line=line_at(ctx.content, cm.start()),
            meta={"namespace": ns, "operation": op_name},
        )
        out.append(c)
    return out


def _extract_factory_calls(
    ctx: ScanContext,
    contracts_by_iface: dict[str, str],
    default_ns: str,
    channel_vars: dict[str, str],
) -> list[Contract]:
    out: list[Contract] = []
    for cv, iface in channel_vars.items():
        ns = contracts_by_iface.get(iface, default_ns)
        out.extend(_extract_channel_var_calls(ctx, iface, ns, cv))
    return out


def _extract_chained_channel_calls(ctx: ScanContext, default_ns: str) -> list[Contract]:
    out: list[Contract] = []
    for cm in _CHAINED_CHANNEL_CALL_RE.finditer(ctx.content):
        if ctx.in_comment(cm.start()):
            continue
        op_name = cm.group("op")
        c = build_contract(
            ctx,
            contract_type="soap",
            contract_id=soap_contract_id(default_ns, op_name),
            role="consumer",
            symbol_name=f"client.{op_name}",
            origin=ContractOrigin.CLIENT_CALL,
            line=line_at(ctx.content, cm.start()),
            meta={"namespace": default_ns, "operation": op_name},
        )
        out.append(c)
    return out


def _extract_consumers(
    ctx: ScanContext, contracts_by_iface: dict[str, str]
) -> list[Contract]:
    default_ns = _resolve_default_ns(ctx, contracts_by_iface)
    factories = _collect_factory_vars(ctx)
    channel_vars = _collect_channel_vars(ctx, factories, contracts_by_iface)
    out = _extract_factory_calls(ctx, contracts_by_iface, default_ns, channel_vars)
    out.extend(_extract_chained_channel_calls(ctx, default_ns))
    return out


class WcfDialect:
    name = "wcf"
    extensions = CSHARP
    requires = ("System.ServiceModel", "CoreWCF")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        providers, contracts_by_iface = _extract_providers(ctx)
        consumers = _extract_consumers(ctx, contracts_by_iface)
        return providers + consumers

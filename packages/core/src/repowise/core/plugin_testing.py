"""Contract checks a plugin author runs against their own implementations.

One call checks a whole plugin, the way ``repowise plugins check`` does::

    from repowise.core.plugin_testing import assert_plugin_passes


    def test_my_plugin_meets_every_contract():
        assert_plugin_passes("repowise_hello:register")

``check_plugin(target)`` returns the same results as data, and
``check_implementation(seam, impl)`` checks one object before it is
registered. Each failure names the seam, the rule broken and the fix.

A check reads the shape repowise calls: every method and attribute of the
seam's protocol, each method's parameters and whether it is ``async``, or the
seam's own type for a seam whose implementations are built from a repowise
class (``Section``, ``ProfileFormat``). It never calls the implementation, so
it needs no service, credentials or index. Behaviour against real data is the
plugin's own tests' job.
"""

from __future__ import annotations

import importlib
import inspect
import typing
from dataclasses import dataclass, field
from typing import Any

#: Seam name -> ``"module:Name"`` of the protocol, class or callable type its
#: implementations follow. Imported only when that seam is checked, so a core
#: install never imports the server. A guard test keeps it in step with every
#: seam repowise defines.
CONTRACTS: dict[str, str] = {
    "advisory-source": "repowise.core.inventory.advisories:AdvisorySource",
    "authenticator": "repowise.server.auth:Authenticator",
    "authorizer": "repowise.server.auth:Authorizer",
    "change-metadata": "repowise.core.changes.source:ChangeMetadataSource",
    "change-outcome": "repowise.core.changes.outcomes:ChangeOutcomeProvider",
    "chat-dataset": "repowise.server.chat_datasets:Dataset",
    "chat-hooks": "repowise.server.chat_seams:TurnHooks",
    "chat-store": "repowise.server.chat_seams:ConversationStoreFactory",
    "chat-tools": "repowise.server.chat_seams:ToolBackendFactory",
    "chat-view": "repowise.server.chat_views:ViewBuilder",
    "document-extractors": "repowise.core.ingestion.document_extractors:DocumentExtractor",
    "event-sink": "repowise.core.events.sinks:EventSink",
    "evidence-source": "repowise.core.evidence.sources:EvidenceSource",
    "first-look-section": "repowise.core.report.first_look.model:FirstLookSection",
    "index-persistence": "repowise.core.pipeline.index_persistence:IndexPersistence",
    "issue-source": "repowise.core.issues.sources:IssueSource",
    "profile-format": "repowise.core.evidence.profile_formats:ProfileFormat",
    "report-renderer": "repowise.core.report.render:Renderer",
    "report-section": "repowise.core.report.model:Section",
    "suspect-signal": "repowise.core.suspects.rank:SuspectSignal",
    "team-directory": "repowise.core.ownership.directory:TeamDirectory",
}


@dataclass(frozen=True)
class ContractFailure:
    """One contract rule an implementation breaks."""

    seam: str
    name: str
    rule: str
    fix: str

    def __str__(self) -> str:
        return f"{self.seam} {self.name}: {self.rule}. Fix: {self.fix}"


@dataclass
class SeamCheck:
    """The result for one thing a plugin registered."""

    plugin: str
    kind: str
    name: str
    #: ``pass``, ``fail``, or ``skipped``: a registry kind (``cli-command``,
    #: ``mcp-tool`` and the rest validate when they register) or a seam a plugin
    #: defines itself.
    status: str
    failures: list[ContractFailure] = field(default_factory=list)


def check_implementation(seam: str, impl: Any, name: str = "") -> list[ContractFailure]:
    """Every rule of *seam*'s contract that *impl* breaks; empty when it passes."""
    name = name or getattr(impl, "name", None) or type(impl).__name__
    if seam not in CONTRACTS:
        return [
            ContractFailure(
                seam,
                str(name),
                f"repowise has no seam named {seam!r}",
                f"register with one of: {', '.join(sorted(CONTRACTS))}",
            )
        ]
    module, _, attr = CONTRACTS[seam].partition(":")
    contract = getattr(importlib.import_module(module), attr)
    if getattr(contract, "_is_protocol", False):
        problems = _check_protocol(contract, impl)
    elif inspect.isclass(contract):
        problems = _check_instance(contract, impl)
    else:
        problems = _check_callable(contract, impl)
    problems += _EXTRA_CHECKS.get(seam, _no_extra)(impl)
    return [ContractFailure(seam, str(name), rule, fix) for rule, fix in problems]


def check_plugin(target: str) -> list[SeamCheck]:
    """Load *target* (``module`` or ``module:callable``) as repowise does, then check it.

    Returns one :class:`SeamCheck` per thing it registered, or a single
    ``load`` failure when it does not load (an import error, a raising
    ``register()``, or a ``MIN_PLUGIN_API_VERSION`` newer than this repowise).
    Every seam implementation and setting the plugin registered is undone
    afterwards, so a test run is not changed by the check, and a plugin that
    already loaded can be checked again; built-in implementations imported
    meanwhile stay, as do commands, tools and hooks it registered.
    """
    from repowise.core.registry import plugins
    from repowise.core.settings.registry import plugin_settings_set_aside

    record = plugins.PluginRecord(target, "check", target)
    saved = {
        name: (dict(r._impls), dict(r._owners), r._chosen) for name, r in plugins.seams().items()
    }
    try:
        with plugin_settings_set_aside():
            with plugins._lock:
                plugins._load(record)
            return check_record(record)
    finally:
        _undo_registrations(record, saved)


def _undo_registrations(record: Any, saved: dict[str, tuple[dict, dict, Any]]) -> None:
    from repowise.core.registry.plugins import seams

    registries = seams()
    for entry in record.seams:
        kind, _, name = entry.partition(":")
        registry = registries.get(kind)
        if registry is None:
            continue  # a registry kind such as cli-command
        impls, owners, chosen = saved.get(kind, ({}, {}, registry._chosen))
        if name in impls:
            registry._impls[name] = impls[name]
        else:
            registry._impls.pop(name, None)
        if name in owners:
            registry._owners[name] = owners[name]
        else:
            registry._owners.pop(name, None)
        registry._chosen = chosen if chosen in registry._impls else None


def check_record(record: Any) -> list[SeamCheck]:
    """Check everything a loaded :class:`~repowise.core.registry.plugins.PluginRecord` registered."""
    from repowise.core.registry.plugins import seams

    if record.status == "failed":
        failure = ContractFailure("load", record.name, "the plugin loads", record.error or "")
        return [SeamCheck(record.name, "load", record.name, "fail", [failure])]
    registries = seams()
    checks = []
    for entry in record.seams:
        kind, _, name = entry.partition(":")
        registry = registries.get(kind)
        if registry is None or kind not in CONTRACTS:  # a registry kind, or a plugin's own seam
            checks.append(SeamCheck(record.name, kind, name, "skipped"))
            continue
        failures = check_implementation(kind, registry._impls.get(name), name)
        checks.append(SeamCheck(record.name, kind, name, "fail" if failures else "pass", failures))
    return checks


def assert_plugin_passes(target: str) -> list[SeamCheck]:
    """:func:`check_plugin`, raising ``AssertionError`` that lists every failure."""
    checks = check_plugin(target)
    failures = [str(f) for c in checks for f in c.failures]
    if failures:
        raise AssertionError(f"{target} breaks the plugin contract:\n" + "\n".join(failures))
    return checks


# ---------------------------------------------------------------------------
# Shapes


def _check_protocol(protocol: type, impl: Any) -> list[tuple[str, str]]:
    cls_name = type(impl).__name__
    problems = []
    for attr in _protocol_attributes(protocol):
        if not hasattr(impl, attr):
            problems.append(
                (
                    f"has no attribute {attr!r}",
                    f"set `{attr}` on {cls_name}, as the protocol declares",
                )
            )
    for method_name, wanted in _protocol_methods(protocol).items():
        method = getattr(impl, method_name, None)
        if not callable(method):
            problems.append(
                (
                    f"has no method {method_name}",
                    f"define `{_signature_text(method_name, wanted)}` on {cls_name}",
                )
            )
            continue
        problems += _check_method(method_name, wanted, method)
    return problems


def _check_method(method_name: str, wanted: Any, method: Any) -> list[tuple[str, str]]:
    want_async = inspect.iscoroutinefunction(wanted)
    is_async = inspect.iscoroutinefunction(method)
    if want_async != is_async:
        how = "an `async def`" if want_async else "a plain `def` (not async)"
        return [
            (f"{method_name} is {'not ' if want_async else ''}async", f"make `{method_name}` {how}")
        ]
    if not _accepts(method, _parameters(wanted)):
        return [
            (
                f"{method_name} does not accept the arguments repowise passes",
                f"give it the signature `{_signature_text(method_name, wanted)}`",
            )
        ]
    return []


def _check_instance(cls: type, impl: Any) -> list[tuple[str, str]]:
    if not isinstance(impl, cls):
        return [
            (
                f"is a {type(impl).__name__}, not a {cls.__name__}",
                f"build it with {cls.__module__}.{cls.__name__}(...)",
            )
        ]
    problems = []
    for attr, f in getattr(cls, "__dataclass_fields__", {}).items():
        value = getattr(impl, attr, None)
        if "Callable" in str(f.type) and value is not None and not callable(value):
            problems.append((f"{attr} is not callable", f"pass a function as `{attr}`"))
    return problems


def _check_callable(alias: Any, impl: Any) -> list[tuple[str, str]]:
    args = typing.get_args(alias)
    count = len(args[0]) if args and isinstance(args[0], list) else 0
    if not callable(impl):
        return [("is not callable", f"register a function taking {count} positional arguments")]
    if not _accepts(
        impl, [inspect.Parameter(f"a{i}", inspect.Parameter.POSITIONAL_ONLY) for i in range(count)]
    ):
        return [
            (
                f"does not accept the {count} positional arguments repowise passes",
                f"give it {count} positional parameters",
            )
        ]
    return []


def _protocol_attributes(protocol: type) -> list[str]:
    names: list[str] = []
    for klass in reversed(protocol.__mro__):
        if klass is object or klass is typing.Generic or not getattr(klass, "_is_protocol", False):
            continue
        names += [n for n in klass.__dict__.get("__annotations__", {}) if not n.startswith("_")]
    return list(dict.fromkeys(names))


def _protocol_methods(protocol: type) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for klass in reversed(protocol.__mro__):
        if klass is object or not getattr(klass, "_is_protocol", False):
            continue
        for name, value in klass.__dict__.items():
            if not name.startswith("_") and inspect.isfunction(value):
                methods[name] = value
    return methods


def _parameters(function: Any) -> list[inspect.Parameter]:
    params = list(inspect.signature(function).parameters.values())[1:]  # self
    return [p for p in params if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]


def _accepts(function: Any, params: list[inspect.Parameter]) -> bool:
    """Whether *function* can be called with every one of *params*, as declared."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return True  # a builtin with no signature: nothing to read
    args = [None for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    kwargs = {p.name: None for p in params if p.kind is p.KEYWORD_ONLY}
    try:
        signature.bind(*args, **kwargs)
    except TypeError:
        return False
    return True


def _signature_text(name: str, function: Any) -> str:
    params = list(inspect.signature(function).parameters.values())
    body = inspect.Signature(params).replace(return_annotation=inspect.Signature.empty)
    prefix = "async def " if inspect.iscoroutinefunction(function) else "def "
    return f"{prefix}{name}{body}"


# ---------------------------------------------------------------------------
# Seam rules beyond the shape


def _no_extra(impl: Any) -> list[tuple[str, str]]:
    return []


def _document_extractor(impl: Any) -> list[tuple[str, str]]:
    from repowise.core.ingestion.document_extractors import _validate_document_extractor

    try:
        _validate_document_extractor(getattr(impl, "name", None) or type(impl).__name__, impl)
    except ValueError as exc:
        return [(str(exc), "see the document-extractors rules in docs/reference/PLUGINS_REFERENCE.md")]
    return []


def _evidence_source(impl: Any) -> list[tuple[str, str]]:
    from repowise.core.evidence.kinds import evidence_kinds

    kinds = getattr(impl, "kinds", None)
    if not isinstance(kinds, tuple) or not kinds:
        return [
            (
                "kinds is not a non-empty tuple",
                'set `kinds = ("statement_stats",)` to the kinds its batches carry',
            )
        ]
    unknown = [k for k in kinds if k not in evidence_kinds]
    if unknown:
        return [
            (
                f"declares unknown evidence kinds {unknown}",
                f"use kinds repowise reads: {', '.join(sorted(evidence_kinds))}",
            )
        ]
    return []


_EXTRA_CHECKS = {
    "document-extractors": _document_extractor,
    "evidence-source": _evidence_source,
}


__all__ = [
    "CONTRACTS",
    "ContractFailure",
    "SeamCheck",
    "assert_plugin_passes",
    "check_implementation",
    "check_plugin",
    "check_record",
]

"""Salesforce flow metadata: the flow's symbol and the names it reaches.

A flow file (``flows/<ApiName>.flow-meta.xml``, legacy
``flows/<ApiName>-<version>.flow``) is one ``<Flow>`` document. The API name is
the file stem, and it is what a subflow, ``Flow.Interview.<Name>``, a quick
action or ``<lightning-flow flow-api-name>`` calls the flow by. This module
reads the file once into a :class:`FlowRefs`: ``special_handlers`` turns it
into the flow's symbol and ``framework_edges/salesforce_metadata.py`` resolves
its names into edges. A triggering object is a schema reference
(``salesforce_schema``).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .models import FileInfo, ParsedFile, SchemaReference, Symbol

# A legacy metadata API flow carries its version number: ``Foo-3.flow``.
_LEGACY_VERSION_RE = re.compile(r"-\d+$")
# Orchestrator stage steps run a flow; their other actionTypes are platform work.
_STEP_ACTION_TYPES = frozenset({"stepinteractive", "stepbackground", "stepapproval"})


@dataclass
class FlowRefs:
    """The names one flow file reaches, as written (case kept)."""

    process_type: str = ""
    trigger_type: str = ""
    start_object: str = ""  # record-triggered and platform-event flows
    label: str = ""
    description: str = ""
    apex_actions: list[str] = field(default_factory=list)  # actionType apex, apexPluginCalls
    flows: list[str] = field(default_factory=list)  # subflows, actionType flow, stage steps
    components: list[str] = field(default_factory=list)  # screen fields, local actions (``c:comp``)
    apex_types: list[str] = field(default_factory=list)  # Apex-defined variable types


def flow_api_name(path: str) -> str:
    """The API name a flow file declares: its stem, legacy version dropped."""
    name = PurePosixPath(path).name
    if name.lower().endswith(".flow-meta.xml"):
        return name[: -len(".flow-meta.xml")]
    return _LEGACY_VERSION_RE.sub("", name.rpartition(".")[0])


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _child_text(el: ET.Element, name: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _read_element(refs: FlowRefs, el: ET.Element) -> None:
    """Add the names one top-level flow element reaches to *refs*."""
    tag = _local(el.tag)
    if tag == "actionCalls":
        _read_action(refs, el)
    elif tag == "apexPluginCalls":
        # Legacy Process.Plugin: the class is the action, not a variable type.
        if name := _child_text(el, "apexClass"):
            refs.apex_actions.append(name)
        return
    elif tag == "subflows":
        if name := _child_text(el, "flowName"):
            refs.flows.append(name)
    elif tag == "start":
        refs.start_object = _child_text(el, "object")
        refs.trigger_type = _child_text(el, "triggerType")
    elif tag == "orchestratedStages":
        refs.flows.extend(_stage_step_flows(el))
    for sub in el.iter():
        sub_tag = _local(sub.tag)
        text = (sub.text or "").strip()
        if sub_tag == "extensionName" and text:
            refs.components.append(text)
        elif sub_tag == "apexClass" and text:
            refs.apex_types.append(text)


def _read_action(refs: FlowRefs, el: ET.Element) -> None:
    name = _child_text(el, "actionName")
    if not name:
        return
    target = {
        "apex": refs.apex_actions,
        "flow": refs.flows,
        # A local action: an Aura bundle (``c:showToast``) the flow runs.
        "component": refs.components,
    }.get(_child_text(el, "actionType").lower())
    if target is not None:
        target.append(name)


def _stage_step_flows(stage: ET.Element) -> list[str]:
    """The flows an Orchestrator stage's steps run."""
    return [
        name
        for step in stage.iter()
        if _local(step.tag) == "stageSteps"
        and _child_text(step, "actionType").lower() in _STEP_ACTION_TYPES
        and (name := _child_text(step, "actionName"))
    ]


def read_flow(source: bytes) -> FlowRefs | None:
    """The names *source* reaches, or None when it is not a well-formed ``<Flow>``."""
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return None
    if _local(root.tag) != "Flow":
        return None
    refs = FlowRefs(
        process_type=_child_text(root, "processType"),
        label=_child_text(root, "label"),
        description=_child_text(root, "description"),
    )
    for el in root:
        _read_element(refs, el)
    return refs


def _signature(name: str, refs: FlowRefs) -> str:
    detail = [refs.process_type] if refs.process_type else []
    if refs.start_object:
        trigger = refs.trigger_type or "triggered"
        detail.append(f"{trigger} on {refs.start_object}")
    return f"flow {name}" + (f" ({', '.join(detail)})" if detail else "")


def parse_flow_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """One function-kind symbol for the flow, named by its API name."""
    refs = read_flow(source)
    if refs is None:
        return ParsedFile(
            file_info=file_info,
            symbols=[],
            imports=[],
            exports=[],
            docstring=None,
            parse_errors=["not a well-formed <Flow> document"],
        )
    name = flow_api_name(file_info.path)
    line_count = source.count(b"\n") + 1
    symbol = Symbol(
        id=f"{file_info.path}::{name}",
        name=name,
        qualified_name=name,
        kind="function",
        signature=_signature(name, refs),
        start_line=1,
        end_line=line_count,
        docstring=refs.description or refs.label or None,
        visibility="public",
        language="salesforce_flow",
    )
    schema_refs = []
    if refs.start_object:
        line = source.count(b"\n", 0, max(source.find(b"<start>"), 0)) + 1
        schema_refs.append(SchemaReference(symbol.id, refs.start_object, line))
    return ParsedFile(
        file_info=file_info,
        symbols=[symbol],
        imports=[],
        exports=[name],
        docstring=refs.label or None,
        parse_errors=[],
        schema_refs=schema_refs,
    )

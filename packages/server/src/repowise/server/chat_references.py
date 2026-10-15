"""Tool results a chat view may cite, across the whole conversation.

``show_view`` names its source by ``artifact_id``. An id is minted when a tool
result enters a turn and stored on that call's artifact envelope, so
:class:`ArtifactIndex` reads the conversation's stored envelopes once per turn
and then takes each new result of the turn as it arrives. A reference may also
be ``latest:<tool_name>``, the newest usable result of that tool, so the model
never has to copy an id. The id is the first key of every result the model
sees (live or replayed), so a truncated excerpt still shows it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from repowise.server.chat_artifacts import ARTIFACT_ID_FIELD, normalize_message_artifacts

SHOW_VIEW_TOOL = "show_view"
#: The chat-only tool that charts a dataset in one call (:mod:`repowise.server.chat_chart`).
CHART_TOOL = "chart"
#: Tools whose result is a view of data, never data a view may cite.
VIEW_TOOLS = frozenset({SHOW_VIEW_TOOL, CHART_TOOL})
LATEST_PREFIX = "latest:"
#: References an unknown-id error lists, newest first.
_LISTED = 8


@dataclass(frozen=True)
class ViewArtifact:
    """One tool result of this conversation, as builders read it."""

    artifact_id: str
    tool_name: str
    artifact_type: str
    evidence_basis: str
    arguments: dict[str, Any]
    data: dict[str, Any]

    @property
    def usable(self) -> bool:
        return "error" not in self.data


def stamp(data: Mapping[str, Any], artifact_id: str) -> dict[str, Any]:
    """*data* with ``artifact_id`` as its first key; the stamp replaces a tool's own field."""
    return {
        ARTIFACT_ID_FIELD: artifact_id,
        **{key: value for key, value in data.items() if key != ARTIFACT_ID_FIELD},
    }


def replayed_result(call: Mapping[str, Any]) -> Any:
    """A stored call's result as the model sees it again: stamped when a view can cite it."""
    artifact = call.get("artifact")
    data = artifact.get("data", {}) if isinstance(artifact, Mapping) else {}
    if (
        call.get("name") not in VIEW_TOOLS
        and isinstance(data, Mapping)
        and "error" not in data
        and isinstance(artifact.get("id"), str)
    ):
        return stamp(data, artifact["id"])
    return data


def _age(turns_ago: int) -> str:
    if turns_ago == 0:
        return "this turn"
    return "1 turn ago" if turns_ago == 1 else f"{turns_ago} turns ago"


class ArtifactIndex:
    """Every citable tool result of one conversation, oldest first."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[ViewArtifact, int]] = {}
        #: Earlier turns' view parts by id, the newest turn's winning: view
        #: ids restart each turn, and "the chart above" means the latest one.
        self.views: dict[str, dict[str, Any]] = {}
        #: The current turn's number; earlier turns are 1..turn-1.
        self.turn = 1

    @classmethod
    def from_history(cls, messages: Iterable[Any]) -> ArtifactIndex:
        """Index the envelopes stored on *messages* (``id``, ``role``, ``content``)."""
        index = cls()
        for message in messages:
            if message.role != "assistant":
                continue
            content = normalize_message_artifacts(message.content, message_id=str(message.id))
            for call in content.get("tool_calls") or []:
                if isinstance(call, dict):
                    index._add_stored(call)
            for part in content.get("views") or []:
                if isinstance(part, dict) and isinstance(part.get("id"), str):
                    index.views[part["id"]] = part
            index.turn += 1
        return index

    def _add_stored(self, call: dict[str, Any]) -> None:
        artifact = call.get("artifact")
        if call.get("name") in VIEW_TOOLS or not isinstance(artifact, dict):
            return
        artifact_id, data = artifact.get("id"), artifact.get("data")
        if not isinstance(artifact_id, str) or not isinstance(data, dict):
            return
        evidence = artifact.get("evidence")
        arguments = call.get("arguments")
        self.add(
            ViewArtifact(
                artifact_id=artifact_id,
                tool_name=str(call.get("name", "")),
                artifact_type=str(artifact.get("type") or "generic"),
                evidence_basis=str((evidence or {}).get("basis", "unknown"))
                if isinstance(evidence, dict)
                else "unknown",
                arguments=arguments if isinstance(arguments, dict) else {},
                data=data,
            )
        )

    def add(self, artifact: ViewArtifact) -> None:
        self._entries[artifact.artifact_id] = (artifact, self.turn)

    def resolve(self, reference: Any) -> ViewArtifact | None:
        """The artifact an id or ``latest:<tool_name>`` names, or None."""
        if not isinstance(reference, str):
            return None
        if reference.startswith(LATEST_PREFIX):
            tool = reference[len(LATEST_PREFIX) :].strip()
            return next(
                (
                    artifact
                    for artifact, _ in reversed(self._entries.values())
                    if artifact.tool_name == tool and artifact.usable
                ),
                None,
            )
        entry = self._entries.get(reference)
        return entry[0] if entry else None

    def available(self) -> list[dict[str, str]]:
        """The newest usable references, for an error the model can retry from."""
        listed: list[dict[str, str]] = []
        for artifact, turn in reversed(self._entries.values()):
            if not artifact.usable:
                continue
            listed.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "tool": artifact.tool_name,
                    "age": _age(self.turn - turn),
                }
            )
            if len(listed) == _LISTED:
                break
        return listed

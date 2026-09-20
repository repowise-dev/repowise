"""OmissionCollector — reversible drops for MCP tool responses.

Dropped content is persisted to the same durable omission store used by the
CLI and advertised through ``_meta.omitted``. Clients recover it with either
``repowise expand <ref>`` or ``get_symbol("repowise#<ref>")``.

The store is best-effort. A failed write returns the reduced tool response with
an explicit ``_meta.recovery_unavailable`` warning and never advertises a dead
reference. The SQLite handle opens lazily and closes in :meth:`attach`.
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
from typing import Any

from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.markers import render_marker
from repowise.core.distill.store import OmissionStore, default_store_path
from repowise.server.mcp_server._references import omission_reference

logger = logging.getLogger(__name__)

#: One sentence, repeated verbatim in every ``_meta.omitted`` block, telling
#: shell-less clients (e.g. Claude Desktop) how to recover without a CLI.
_RESTORE_HINT = (
    "Run `repowise expand <ref>` from the repo, or call "
    'get_symbol("repowise#<ref>", query=...) to retrieve the omitted content.'
)

_DOC_SECTION_RULE = "==== {label} ===="
def render_chunk(label: str, value: Any) -> str:
    """Render one dropped value as line-oriented text for the omission doc.

    Strings pass through verbatim; everything else becomes indented JSON so
    ``expand --query`` line filtering stays useful on structured drops.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=1, default=str)


def cap_collection(
    container: dict[str, Any],
    key: str,
    population: list[Any],
    limit: int,
    collector: OmissionCollector | None = None,
    *,
    emitted: list[Any] | None = None,
    label: str | None = None,
    reason: str = "construction_cap",
    preserve_counts: bool = False,
) -> list[Any]:
    """Keep a deterministic head while making every real reduction recoverable.

    ``population`` is the complete post-policy population.  ``emitted`` may
    supply a deliberately diversified projection (for example, retaining one
    transitive dependent beside direct ones); omitted rows are then computed as
    a stable multiset difference instead of assuming a simple tail.

    Small collections stay compact unless ``preserve_counts`` keeps an existing
    public count family.  A reduced collection always receives the shared
    total/emitted/reason fields plus compatibility truncation counts, and its
    exact omitted rows are queued on the caller's :class:`OmissionCollector`.
    """
    full = list(population)
    visible = list(emitted) if emitted is not None else full[:limit]
    container[key] = visible
    total = len(full)
    emitted_count = len(visible)
    reduced = emitted_count < total

    if preserve_counts or reduced:
        container[f"{key}_total"] = total
        container[f"{key}_emitted"] = emitted_count
        container[f"{key}_truncated"] = reduced
    if not reduced:
        return visible

    remaining = list(full)
    for row in visible:
        with contextlib.suppress(ValueError):
            remaining.remove(row)
    container[f"{key}_reduced_reason"] = reason
    container[f"{key}_omitted"] = len(remaining)
    if collector is not None and remaining:
        collector.add(label or f"{key} beyond cap={limit}", remaining)
    return visible


class OmissionCollector:
    """Collects content dropped from one MCP tool response.

    Two capture modes:

    * :meth:`add` — queue a chunk into a single combined omission document,
      stored at :meth:`attach` time with one marker for the whole response.
    * :meth:`add_inline` — store a chunk immediately as its own row and
      return its marker, for drops whose marker must replace the content
      in-place (e.g. a stripped skeleton text).

    ``store_path`` overrides the resolved store location (tests). Otherwise
    the store is resolved from ``repo_root`` exactly like the CLI: the repo's
    own ``.repowise/`` when present, the user-level fallback otherwise.
    """

    def __init__(
        self,
        tool: str,
        repo_root: str | Path | None = None,
        *,
        store_path: str | Path | None = None,
    ) -> None:
        self.tool = tool
        if store_path is not None:
            self._store_path = Path(store_path)
        else:
            self._store_path = default_store_path(Path(repo_root) if repo_root else None)
        # Keep values live until attach. Why-mode evidence annotation happens
        # after ranking but before delivery; eagerly rendering here would freeze
        # omitted rows before they receive provenance and evidence refs.
        self._chunks: list[tuple[str, Any]] = []
        self._refs: list[str] = []
        self._omitted_tokens = 0
        self._store: OmissionStore | None = None
        self._store_failed = False

    # -- capture -------------------------------------------------------------

    def add(self, label: str, value: Any) -> None:
        """Queue a dropped chunk for the combined omission document."""
        if value is not None:
            self._chunks.append((label, value))

    def add_inline(self, label: str, content: str) -> str | None:
        """Store *content* as its own row and return its marker.

        Returns ``None`` when the store is unavailable — the caller must then
        fall back to its pre-collector drop behaviour (content stored before
        marker rendered; no row, no marker).
        """
        if not content:
            return None
        ref = self._put(content)
        if ref is None:
            return None
        tokens = estimate_tokens(content)
        self._omitted_tokens += tokens
        return render_marker(ref, content.count("\n") + 1, tokens)

    @property
    def empty(self) -> bool:
        """True when nothing has been captured (queued or stored)."""
        return not self._chunks and not self._refs

    # -- finalisation ----------------------------------------------------------

    def attach(self, response: dict[str, Any]) -> None:
        """Persist queued chunks and stamp *response* with refs and markers.

        Adds (only when something was captured AND stored successfully):
          * ``response["omission_marker"]`` — marker for the combined document
            of queued chunks, if any;
          * ``response["_meta"]["omitted"]`` — ``{refs, tokens, restore}``
            covering every ref this collector produced.

        Never raises; always closes the store handle.
        """
        try:
            if self._chunks:
                doc = self._render_doc()
                ref = self._put(doc)
                if ref is not None:
                    tokens = estimate_tokens(doc)
                    self._omitted_tokens += tokens
                    response["omission_marker"] = render_marker(
                        ref, doc.count("\n") + 1, tokens
                    )
                elif self._chunks:
                    response.setdefault("_meta", {})["recovery_unavailable"] = (
                        "Omission storage failed; dropped evidence could not be persisted."
                    )
            if self._refs:
                meta = response.setdefault("_meta", {})
                existing = meta.get("omitted")
                existing_refs = (
                    list(existing.get("refs") or []) if isinstance(existing, dict) else []
                )
                existing_tokens = (
                    int(existing.get("tokens") or 0) if isinstance(existing, dict) else 0
                )
                meta["omitted"] = {
                    "refs": [
                        normalized
                        for value in [*existing_refs, *self._refs]
                        if (normalized := omission_reference(value)) is not None
                    ],
                    "tokens": existing_tokens + self._omitted_tokens,
                    "restore": _RESTORE_HINT,
                }
        except Exception:  # pragma: no cover - defensive
            logger.warning("omission collector attach failed", exc_info=True)
        finally:
            self.close()

    def close(self) -> None:
        if self._store is not None:
            with contextlib.suppress(Exception):
                self._store.close()
            self._store = None

    # -- internals -------------------------------------------------------------

    def _render_doc(self) -> str:
        head = (
            f"[repowise omissions] tool={self.tool}\n"
            "Content below was dropped from an MCP response to fit the "
            "transport budget; sections are verbatim.\n"
        )
        parts = [head]
        for label, value in self._chunks:
            text = render_chunk(label, value)
            if not text:
                continue
            parts.append(_DOC_SECTION_RULE.format(label=label))
            parts.append(text)
        return "\n".join(parts)

    def _put(self, content: str) -> str | None:
        """Write *content* to the store; None on any failure (degrade silently)."""
        if self._store_failed:
            return None
        try:
            if self._store is None:
                self._store = OmissionStore(self._store_path)
            ref = self._store.put(
                content,
                source=f"mcp:{self.tool}",
                original_tokens=estimate_tokens(content),
                kept_tokens=0,
            )
        except Exception:
            logger.warning(
                "omission store write failed; falling back to silent drop",
                exc_info=True,
            )
            self._store_failed = True
            return None
        self._refs.append(ref)
        return ref

"""StyleSpec — the value object describing one wiki documentation style.

A *style* governs the **voice and density** of generated wiki pages, never their
structural contract: every style still emits the same required ``##`` headings and
section skeleton (see WIKI_STYLES_PLAN.md, decision D3). Styles differ in two ways:

* ``user_directive`` — a constant block **prepended to the user prompt** of every
  LLM page. This is the primary lever: the model reads it and adjusts its prose.
* ``system_note`` — a constant block **appended to the system prompt**, used to
  re-frame the base "comprehensive, accurate" instruction when a style needs a
  different posture (e.g. "be terse").

The class is deliberately data-only. All built-in styles are declared in
``registry.py``; resolution (built-in, and later custom ``.repowise/styles/``)
goes through ``resolve_style``.

Cache contract (the load-bearing detail — see WIKI_STYLES_PLAN.md §2):
the per-style ``fingerprint`` is woven into the rendered user prompt for active
styles, so a style change flows into each page's ``source_hash`` and the existing
incremental-update machinery regenerates exactly the affected pages. The
``comprehensive`` style is inert (empty directive + note) and therefore produces
byte-identical prompts to the pre-feature behaviour — existing wikis never
regenerate spuriously when the feature lands.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# The page_type used by the curated onboarding collection. Onboarding pages are
# narrative by design; whether a style condenses them is a per-style policy (D9).
ONBOARDING_PAGE_TYPE = "onboarding"


@dataclass(frozen=True)
class StyleSpec:
    """One wiki documentation style (voice + density), resolved at generation time.

    Attributes:
        name:                 Stable identifier typed by users (CLI/config value).
        description:          One-line, user-facing summary (shown in selectors).
        is_builtin:           True for shipped styles; False for ``.repowise/styles``.
        user_directive:       Constant block prepended to every LLM user prompt.
                              Empty string = inert (the ``comprehensive`` baseline).
        system_note:          Constant block appended to every system prompt.
        onboarding_condenses: When False, the onboarding collection keeps the
                              baseline narrative voice even under this style (D9).
        template_dir:         Optional per-style template directory (Layer 2 /
                              custom styles). ``None`` = use the base templates.
        style_version:        Bump to force regeneration when a style's text
                              changes in a way the fingerprint must capture.
    """

    name: str
    description: str
    is_builtin: bool = True
    user_directive: str = ""
    system_note: str = ""
    onboarding_condenses: bool = False
    template_dir: Path | None = None
    style_version: int = 1

    @property
    def is_active(self) -> bool:
        """True when this style changes output vs the inert baseline.

        An inert style (no directive and no note) renders prompts identically to
        the pre-feature code path, so it must contribute nothing to any hash.
        """
        return bool(self.user_directive or self.system_note)

    @property
    def fingerprint(self) -> str:
        """Short content hash over everything that affects this style's output.

        Empty for inert styles (so they leave prompt hashes untouched). For active
        styles it covers name, version, directive, and note — so editing any of
        them invalidates cached pages on the next update.
        """
        if not self.is_active:
            return ""
        h = hashlib.sha256()
        h.update(self.name.encode())
        h.update(str(self.style_version).encode())
        h.update(self.user_directive.encode())
        h.update(self.system_note.encode())
        return h.hexdigest()[:16]

    def _applies_to(self, *, is_onboarding: bool) -> bool:
        """Whether this style's voice applies to a page of the given kind."""
        if not self.is_active:
            return False
        return not (is_onboarding and not self.onboarding_condenses)

    def user_prompt_prefix(self, *, is_onboarding: bool) -> str:
        """Block to prepend to a page's user prompt (``""`` to leave it untouched).

        The leading marker comment carries the fingerprint so that *any* change to
        the style — including a ``system_note``-only change that wouldn't otherwise
        alter the user prompt — flows into ``source_hash`` and triggers regen.
        """
        if not self._applies_to(is_onboarding=is_onboarding):
            return ""
        marker = f"<!-- repowise-style:{self.name} fp:{self.fingerprint} -->"
        body = self.user_directive.strip()
        block = marker if not body else f"{marker}\n{body}"
        return f"{block}\n\n"

    def system_prompt_suffix(self, *, is_onboarding: bool) -> str:
        """Block to append to a page's system prompt (``""`` to leave it untouched)."""
        if not self._applies_to(is_onboarding=is_onboarding):
            return ""
        note = self.system_note.strip()
        return f"\n\n{note}" if note else ""

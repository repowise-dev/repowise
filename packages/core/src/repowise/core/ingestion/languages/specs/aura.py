"""LanguageSpec for Salesforce Aura component markup, an edge-only tag.

An Aura component is a bundle directory ``aura/<name>/`` holding markup
(``<name>.cmp``, ``.app``, ``.evt``, ``.intf``, ``.design``, ``.tokens``) and
JavaScript (``<name>Controller.js``, ``<name>Helper.js``,
``<name>Renderer.js``). The JavaScript stays ``javascript``. The markup is
what names everything else: the Apex ``controller=`` class, the ``{!c.h}``
controller functions, the ``<c:child>`` bundles, ``extends=``, ``implements=``
and the ``.evt`` event types. Without the markup indexed, a bundle's
JavaScript and its Apex controller have no inbound edge at all.

``is_code=False`` for the same reason as ``html`` and ``godot_resource``:
markup declares no symbols, and the classification keeps dead code from
*reporting* a component nobody instances (flexipages, tabs, quick actions and
App Builder place components from outside the source tree) while its
outbound edges still anchor everything it names. The edges come from
``framework_edges/aura.py``; there are no imports for a resolver to read.

No extension claim. ``.app``, ``.design``, ``.tokens`` (ANTLR writes
``Lexer.tokens``) and ``.evt`` are not Aura's alone, and one extension maps to
one language for the whole repository, so the traverser routes a file here by
path instead: one of these suffixes directly inside ``aura/<bundle>/``.
"""

from pathlib import PurePosixPath

from ..spec import LanguageSpec

AURA_MARKUP_SUFFIXES = frozenset({".cmp", ".app", ".evt", ".intf", ".design", ".tokens"})


def aura_bundle(path: str) -> tuple[str, str] | None:
    """``(bundle_dir, bundle_name)`` for a file directly inside ``aura/<bundle>/``."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) < 3 or parts[-3] != "aura":
        return None
    return "/".join(parts[:-1]), parts[-2]


def is_aura_markup_path(path: str) -> bool:
    """True for an Aura markup file: a markup suffix inside an ``aura/<bundle>/`` dir."""
    return PurePosixPath(path).suffix.lower() in AURA_MARKUP_SUFFIXES and aura_bundle(path) is not None


SPEC = LanguageSpec(
    tag="aura",
    display_name="Aura",
    # Routed by path, not by extension; see the module docstring.
    extensions=frozenset(),
    is_code=False,
    is_passthrough=True,
    # Salesforce blue, the same as the Apex spec.
    color_hex="#1797C0",
)

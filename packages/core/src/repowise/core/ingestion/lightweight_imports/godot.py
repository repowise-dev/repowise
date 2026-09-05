"""Dependency extraction for Godot's resource files.

``.tscn`` / ``.tres`` / ``.escn``, ``project.godot`` and an addon's
``plugin.cfg`` are one line-anchored ini dialect, and every construct that
carries a dependency is a single line with a quoted path on it::

    [ext_resource type="Script" path="res://actors/player.gd" id="1_4s751"]
    [ext_resource path="res://actors/player.gd" type="Script" id=1]   # Godot 3

    [autoload]
    Events="*res://global/events.gd"

    [application]
    run/main_scene="res://scenes/run/run.tscn"

    [plugin]
    script="plugin.gd"

so a regex is the right rung and no second tree-sitter grammar is needed. One
extractor covers all three file shapes because the section names disambiguate
them without a filename: ``[ext_resource]`` appears only in a scene or
resource, ``[autoload]`` / ``[application]`` only in ``project.godot``,
``[plugin]`` only in a ``plugin.cfg``.

**Invariant ``graph_warmups._warmup_godot`` depends on:** every import this
extractor emits from a ``project.godot`` or a ``plugin.cfg`` is a declaration
that the *engine* starts there: an ``[autoload]`` singleton it instantiates
before the first scene, the ``run/main_scene`` it boots into, or the
``EditorPlugin`` subclass the editor loads when the plugin is enabled. The
warmup stamps those files' resolved imports as entry points wholesale rather
than re-reading and re-parsing them. Adding a key here that is not an
execution start means teaching the warmup to tell them apart.

``plugin.cfg``'s ``script`` is relative to the plugin's own directory, not
``res://``; the resolver's importer-relative fallback covers that. It matters
most for a repo that *publishes* an addon (``dialogic``): the ``EditorPlugin``
is then the only declared entry point it has.

``script = ExtResource("1_4s751")`` on a node needs no handling of its own:
it dereferences an id whose ``[ext_resource]`` header already carries the path.

A scene's ``[ext_resource]`` list also names every texture, sound and font the
scene uses, thousands per repo, so it is filtered here to
``resolvers.gdscript.GODOT_CODE_SUFFIXES``. That is a **whitelist**, unlike
the ``.gd`` side, which drops assets only on the resolver's miss path: a
reference this list does not recognise (Godot 3's ``.shader``, GDNative's
``.gdns``, the binary ``.scn`` / ``.res`` forms) vanishes from a scene with no
edge and no external node, where the same reference in a ``preload`` would
survive as an external node. Documented in ``LANGUAGE_SUPPORT.md``; widening
the list is the fix if one of those turns up in a real repo.

Known ceilings, all recorded as absent edges rather than guesses:

* Godot 4.4 may write ``uid="uid://..."`` with no ``path=``. Resolving one
  needs the generated ``.uid`` sidecars the spec blocks from indexing. Not
  observed on the validation corpus. An independent ``grep`` over the four
  repos counted 1150 ``[ext_resource`` lines and 1150 carrying ``path=``.
  (This module cannot establish that itself: its pattern only matches lines
  that have ``path=``, so a path-less header is invisible to it.)
* An autoload registered programmatically,
  ``add_autoload_singleton("Foo", "res://foo.gd")`` from an ``EditorPlugin``,
  which is how ``dialogic`` installs itself, appears in no ``[autoload]``
  table and cannot be seen here.
* ``project.godot``'s ``[editor_plugins] enabled=`` is not read, so there is
  no project → ``plugin.cfg`` edge. It names ``.cfg`` files, which carry no
  code; the plugin's *script* is reached from the ``plugin.cfg`` itself.
"""

from __future__ import annotations

import re

from ..models import Import

# A whitelist here; the .gd path uses an asset blacklist on the resolver's
# miss path instead. The two agree on the case that matters (a `.png` gets no
# edge either way) and differ on an unrecognised suffix -- see the docstring.
from ..resolvers.gdscript import GODOT_CODE_SUFFIXES

# An ext_resource *header* line. Attribute order varies between Godot 3 and 4,
# so this scans the whole header for `path=` rather than assuming a position.
# The editor writes double quotes; single quotes are accepted because a
# hand-edited or tool-generated scene may use them, and the backreference
# keeps the pair matched.
_EXT_RESOURCE_RE = re.compile(
    r"""^\[ext_resource\b[^\]]*?\bpath\s*=\s*(["'])(.+?)\1"""
)

# A bare section header: `[autoload]`, `[application]`. Deliberately anchored
# and closed so it cannot match an `[ext_resource ...]` header, which carries
# attributes after the name.
_SECTION_RE = re.compile(r"^\[([A-Za-z_][\w.]*)\]\s*$")

# `Events="*res://global/events.gd"`. The leading `*` marks the entry as an
# enabled singleton; it is not part of the path.
_AUTOLOAD_RE = re.compile(r'^\s*[\w.]+\s*=\s*"\*?([^"]+)"')

# `run/main_scene="res://scenes/run/run.tscn"`: the scene the engine loads at
# boot, and the only key in [application] that names a file with code in it.
_MAIN_SCENE_RE = re.compile(r'^\s*run/main_scene\s*=\s*"([^"]+)"')

# `script="plugin.gd"` in a plugin.cfg. Keyed by name rather than by position
# because [plugin] also carries name/description/author/version.
_PLUGIN_SCRIPT_RE = re.compile(r'^\s*script\s*=\s*"([^"]+)"')

# A `key="…` line whose quote never closes: the value continues onto the next
# line. Godot writes these -- dialogic's plugin.cfg description runs to two
# lines, one of which is a bare URL. Every line until the closing quote has to
# be skipped wholesale, or a description containing a line that is exactly
# `[b]` or `[center]` (legal BBCode, and a perfect _SECTION_RE match) would
# silently move the parser out of [plugin] and lose the `script` key below it.
_OPENS_UNCLOSED_VALUE = re.compile(r'^\s*[\w./]+\s*=\s*"[^"]*$')


def _is_code_path(path: str) -> bool:
    # Case-insensitive to match the resolver's asset test, which lowercases.
    return path.lower().endswith(GODOT_CODE_SUFFIXES)


def extract_godot_imports(text: str) -> list[Import]:
    """Return one ``Import`` per script/scene/resource this file references."""
    raw: list[tuple[str, str]] = []

    section = ""
    in_multiline_value = False
    # A UTF-8 BOM would otherwise make line 1 `﻿[plugin]`, which no
    # pattern here matches -- losing the whole file's first section.
    for line in text.lstrip("﻿").splitlines():
        if in_multiline_value:
            in_multiline_value = '"' not in line
            continue
        if _OPENS_UNCLOSED_VALUE.match(line):
            in_multiline_value = True
            continue
        ext_resource = _EXT_RESOURCE_RE.match(line)
        if ext_resource is not None:
            raw.append(("ext_resource", ext_resource.group(2)))
            continue
        header = _SECTION_RE.match(line)
        if header is not None:
            section = header.group(1)
            continue
        if section == "autoload":
            autoload = _AUTOLOAD_RE.match(line)
            if autoload is not None:
                raw.append(("autoload", autoload.group(1)))
        elif section == "application":
            main_scene = _MAIN_SCENE_RE.match(line)
            if main_scene is not None:
                raw.append(("run/main_scene", main_scene.group(1)))
        elif section == "plugin":
            script = _PLUGIN_SCRIPT_RE.match(line)
            if script is not None:
                raw.append(("plugin script", script.group(1)))

    imports: list[Import] = []
    seen: set[str] = set()
    for kind, path in raw:
        if not _is_code_path(path) or path in seen:
            continue
        seen.add(path)
        imports.append(
            Import(
                raw_statement=f'{kind}: "{path}"',
                module_path=path,
                imported_names=[],
                # `res://` is absolute from the Godot project root, never
                # relative to the referencing file. See resolvers/gdscript.py.
                is_relative=False,
                resolved_file=None,
            )
        )
    return imports

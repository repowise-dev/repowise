"""LanguageSpec for Godot's resource files, an import-tier language.

Covers ``.tscn`` (scenes), ``.tres`` (resources), ``.escn`` (Blender-exported
scenes), ``project.godot`` and an addon's ``plugin.cfg``. All are the same
line-anchored ini dialect, so one tag covers them and
``lightweight_imports/godot.py`` distinguishes them by the section names
present rather than by filename.

**Why this exists at all.** A Godot script is almost never referenced by
another script. It is attached to a node in a scene::

    [ext_resource type="Script" path="res://actors/player.gd" id="1_4s751"]
    ...
    [node name="Player" type="CharacterBody2D"]
    script = ExtResource("1_4s751")

and the scene is instanced by another scene, or named in ``project.godot``'s
``[autoload]`` table. Index the ``.gd`` files alone and the dependency graph
has no edges into most of them, which makes GDScript dead-code reporting
worse than not supporting the language at all, so this ships in the same
release as the GDScript AST, never after it.

**No second grammar.** ``tree-sitter-language-pack`` ships a
``godot_resource`` grammar and it parses these files correctly, but the two
constructs that carry dependencies (an ``[ext_resource]`` header line and an
``autoload`` assignment) are single lines with a quoted path on them. A
regex is the right rung, and it keeps the wheel-publishing problem GDScript
already carries down to exactly one grammar.

``is_code=False`` is truthful and load-bearing, the same way it is for
``html``: these files are data, they declare no symbols, and the
classification puts them in dead code's ``_NON_CODE_LANGUAGES`` so a scene
nobody instances is never *reported* as dead while its outbound edges still
anchor every script it attaches. Whether a scene is reachable is not
statically decidable: ``load()`` takes a runtime string, and the editor
opens scenes directly.

Known ceilings:

* **``uid://``**: Godot 4.4 can write it in place of ``path=`` in an
  ``ext_resource`` header, and resolving one needs the generated ``.uid``
  sidecars this spec blocks from indexing. Recorded as an external reference
  rather than guessed at (see ``resolvers/gdscript.py``). Not yet observed: a
  ``grep`` over the four-repo validation corpus counted 1150 ``[ext_resource``
  lines, all 1150 carrying ``path=``.
* **The binary scene and resource formats ``.scn`` / ``.res``** are not
  covered, not by ``extensions`` here and not by the suffix lists in
  ``resolvers/gdscript.py``. Godot writes text by default and the corpus has
  none, but a project that ships binary scenes gets no extraction from them
  and no edge to one.
* **Unrecognised reference suffixes vanish from a scene** rather than becoming
  external nodes, because the scene side filters with a whitelist. See
  ``lightweight_imports/godot.py``.

Registering the bare filename ``plugin.cfg`` has one side effect worth
naming: an unrelated ``plugin.cfg`` in a non-Godot repo is now classified as
"Godot Resource" in the file tree and language stats. It yields no imports,
not because of its section names, which are ordinary ini, but because the
extractor's suffix whitelist admits only code paths, so it costs a label,
not an edge.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="godot_resource",
    display_name="Godot Resource",
    extensions=frozenset({".tscn", ".tres", ".escn"}),
    # project.godot is also gdscript's `manifest_files` entry, which marks its
    # directory as a package root. The registry keeps the two maps separate
    # (extension/filename vs manifest), so a file can be both: the res:// root
    # for the resolver, and an indexed file whose [autoload] table is a real
    # set of edges.
    # `plugin.cfg` is an addon's manifest; its `script=` names the EditorPlugin
    # subclass the editor loads. See the docstring for what registering the
    # bare name costs outside Godot.
    special_filenames=frozenset({"project.godot", "plugin.cfg"}),
    # Data, not code -- no symbols exist to extract. See the module docstring.
    is_code=False,
    is_passthrough=True,
    # `res://` is absolute from the project root and resolves exactly, via the
    # same resolver the .gd files use. The one gap (uid://) is a Godot
    # build-cache artifact rather than a dialect we fail to read, so this is
    # "full" where html's src/href tier is "partial".
    import_support="full",
    blocked_dirs=(".godot", ".import"),
    blocked_extensions=(".import", ".uid"),
    # Godot's own brand blue, matching the gdscript spec.
    color_hex="#478CBF",
)

"""LanguageSpec for GDScript (Godot Engine).

Grammar: tree-sitter-gdscript (import name ``tree_sitter_gdscript``),
maintained by Preston Knopp
(https://github.com/PrestonKnopp/tree-sitter-gdscript), MIT.

``heritage_node_types`` covers both places a parent can be declared:
``class_name_statement`` (the script-level class, whose grammar carries an
optional ``extends`` field AND may be preceded by a standalone
``extends_statement`` sibling) and ``class_definition`` (inner classes,
which always carry their own ``extends`` field). See
extractors/heritage/gdscript.py for the sibling walk the first case needs.

A .gd file with no ``class_name`` declares an *anonymous* class. It gets no
class symbol and therefore no symbol-level heritage edge -- see the note in
queries/gdscript.scm on why a synthesized file-stem name would produce a
dangling graph node. Its ``extends "res://..."`` still yields a file-level
import edge, which is the edge that actually carries the dependency.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="gdscript",
    display_name="GDScript",
    extensions=frozenset({".gd"}),
    grammar_package="tree_sitter_gdscript",
    scm_file="gdscript.scm",
    heritage_node_types=frozenset({"class_name_statement", "class_definition"}),
    # Dedicated resolver: `res://` is an absolute path from the directory
    # holding project.godot, so resolution is exact rather than a stem guess.
    import_support="full",
    # project.godot both defines the res:// root for the resolver and marks
    # its directory as a package boundary -- godot-demo-projects is one repo
    # holding dozens of separate Godot projects, each with its own root.
    manifest_files=("project.godot",),
    # `.godot/` is the Godot 4 build/import cache and `.import/` its Godot 3
    # equivalent; both are full of generated files. Left unblocked, they
    # dominate any corpus measurement.
    blocked_dirs=(".godot", ".import"),
    # Godot re-imports assets from these and rewrites them without human
    # involvement.
    blocked_extensions=(".import", ".uid"),
    shebang_tokens=(),
    # @GlobalScope / @GDScript global functions plus the built-in *variant*
    # constructors (Vector2, Color, ...). Engine *classes* (Node, Sprite2D,
    # ...) are deliberately absent: those are referenced as receivers
    # (`Node.new()`), not as bare call targets, so listing them here would
    # buy nothing. Conservative on purpose, and it has to be: ASTParser's
    # builtin check is receiver-BLIND (`if target_name in _call_builtins` runs
    # before receiver_name is read), so a name listed here also deletes
    # `obj.name()` method edges. Anything plausible as a project method name
    # is therefore left out -- see `load` below.
    builtin_calls=frozenset({
        # Output / diagnostics. `breakpoint` is deliberately absent: it has a
        # dedicated breakpoint_statement node and never parses as a call.
        "print", "printerr", "printraw", "printt", "prints", "print_rich",
        "push_error", "push_warning", "assert",
        # Conversion / reflection
        "str", "int", "float", "bool", "char", "ord", "hash", "typeof",
        "type_string", "type_exists", "var_to_str", "str_to_var",
        "var_to_bytes", "bytes_to_var", "weakref", "is_instance_valid",
        "is_instance_id_valid", "instance_from_id", "len", "range",
        # `preload` is also captured as an import; excluded here so the call
        # graph does not gain a dangling node beside that edge. `load` is
        # deliberately NOT excluded despite being the same kind of global:
        # the filter is receiver-blind, and `load` is a common project and
        # engine method name (`ConfigFile.load`, `Image.load`, save
        # managers), so listing it would silently delete those call edges.
        "preload",
        # Not a global, but GDScript 4's bare `super()` parses as a plain
        # call whose target can never resolve to a project symbol.
        "super",
        # Math
        "abs", "absi", "absf", "min", "mini", "minf", "max", "maxi", "maxf",
        "clamp", "clampi", "clampf", "round", "roundi", "roundf", "floor",
        "floori", "floorf", "ceil", "ceili", "ceilf", "sign", "signi",
        "signf", "snapped", "snappedi", "snappedf", "fmod", "fposmod",
        "posmod", "wrap", "wrapi", "wrapf", "sqrt", "pow", "exp", "log",
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh",
        "cosh", "tanh", "deg_to_rad", "rad_to_deg", "linear_to_db",
        "db_to_linear", "lerp", "lerpf", "lerp_angle", "inverse_lerp",
        "remap", "smoothstep", "move_toward", "rotate_toward", "ease",
        "step_decimals", "nearest_po2", "pingpong", "is_equal_approx",
        "is_zero_approx", "is_nan", "is_inf", "is_finite",
        # Randomness
        "randi", "randf", "randfn", "randi_range", "randf_range",
        "randomize", "seed", "rand_from_seed",
        # Built-in variant constructors
        "Vector2", "Vector2i", "Vector3", "Vector3i", "Vector4", "Vector4i",
        "Rect2", "Rect2i", "Color", "Color8", "Plane", "Quaternion", "AABB",
        "Basis", "Transform2D", "Transform3D", "Projection", "Callable",
        "Signal", "StringName", "NodePath", "RID", "Dictionary", "Array",
        "PackedByteArray", "PackedInt32Array", "PackedInt64Array",
        "PackedFloat32Array", "PackedFloat64Array", "PackedStringArray",
        "PackedVector2Array", "PackedVector3Array", "PackedColorArray",
    }),
    # The engine root types only, matching the Pascal `TObject` / Swift
    # `NSObject` precedent. Godot ships ~800 classes and enumerating them
    # would be arbitrary and stale within a release; an unlisted engine
    # parent costs nothing, because HeritageResolver drops any relation
    # whose parent name resolves to no symbol in the graph.
    builtin_parents=frozenset({
        "Object", "RefCounted", "Reference", "Resource", "Node",
        "CanvasItem", "Node2D", "Node3D", "Spatial", "Control",
    }),
    # Godot's own brand blue.
    color_hex="#478CBF",
)

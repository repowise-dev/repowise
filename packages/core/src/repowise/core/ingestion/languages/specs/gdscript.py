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
    # The engine API a script calls on implicit `self`. Read only by the
    # bare-name tier in CallResolver, which answers a name declared exactly
    # once anywhere in the repo at 0.50 confidence: without this list, a
    # script's `queue_free()` or `emit_signal(...)` binds to whatever lone
    # project function happens to share the name. Unlike `builtin_calls`
    # above, listing a name here does NOT delete `obj.name()` member edges,
    # which is why the engine API belongs here and the global scope there.
    #
    # Rust's rule applies: a name the project plausibly declares and calls in
    # its own right stays off, because this tier is a guess but not always a
    # wrong one. That is why `play`, `stop`, `start`, `get`, `set`, `call`,
    # `update` and `reset` are absent despite all being engine methods.
    builtin_methods=frozenset({
        # Object: signals, deferred dispatch, reflection, lifetime
        "connect", "disconnect", "is_connected", "emit_signal", "has_signal",
        "call_deferred", "set_deferred", "callv", "has_method",
        "get_class", "is_class", "get_instance_id", "get_script", "set_script",
        "get_meta", "set_meta", "has_meta", "remove_meta", "notification",
        "free", "get_signal_list", "get_method_list", "get_property_list",
        "_init", "_notification", "_get", "_set", "_to_string",
        # Node: tree, children, groups, per-frame processing
        "queue_free", "add_child", "remove_child", "move_child", "reparent",
        "get_child", "get_children", "get_child_count",
        "get_node", "get_node_or_null", "find_child", "find_children",
        "get_parent", "get_owner", "get_tree", "get_window", "get_viewport",
        "is_inside_tree", "is_node_ready", "request_ready", "propagate_call",
        "add_to_group", "remove_from_group", "is_in_group", "get_groups",
        "set_process", "set_physics_process", "set_process_input",
        "set_process_unhandled_input", "set_process_mode", "print_tree",
        "duplicate", "rpc", "rpc_id", "set_multiplayer_authority",
        "is_multiplayer_authority", "get_multiplayer_authority",
        # Node callbacks the engine invokes, never another script
        "_ready", "_process", "_physics_process", "_enter_tree", "_exit_tree",
        "_input", "_unhandled_input", "_unhandled_key_input", "_shortcut_input",
        "_draw", "_gui_input", "_integrate_forces",
        "_get_configuration_warnings",
        # CanvasItem / Node2D / Node3D
        "queue_redraw", "hide", "show", "is_visible_in_tree", "look_at",
        "to_local", "to_global", "get_global_transform", "get_global_position",
        "draw_line", "draw_rect", "draw_circle", "draw_arc", "draw_polygon",
        "draw_colored_polygon", "draw_texture", "draw_texture_rect",
        "draw_string",
        # Physics bodies
        "move_and_slide", "move_and_collide", "is_on_floor", "is_on_wall",
        "is_on_ceiling", "get_slide_collision", "get_slide_collision_count",
        "apply_impulse", "apply_central_impulse", "apply_force",
        "set_collision_layer_value", "set_collision_mask_value",
        # Control
        "grab_focus", "release_focus", "has_focus", "get_rect",
        "get_global_rect", "set_anchors_preset",
        "add_theme_color_override", "add_theme_font_override",
        "add_theme_stylebox_override", "add_theme_constant_override",
        "get_theme_color", "get_theme_font", "get_theme_stylebox",
        # SceneTree
        "change_scene_to_file", "change_scene_to_packed", "reload_current_scene",
        "create_timer", "create_tween", "get_first_node_in_group",
        "get_nodes_in_group", "call_group", "set_group", "get_root",
        # Tween
        "tween_property", "tween_callback", "tween_interval", "tween_method",
        "set_trans", "set_ease", "set_loops",
        # PackedScene / ResourceLoader
        "instantiate", "instance", "take_over_path",
        "load_threaded_request", "load_threaded_get",
        # @GDScript globals. `load` is the one that matters: it is kept out of
        # `builtin_calls` so its argument still records an import, which leaves
        # this list as the only thing standing between a bare `load(...)` and a
        # project's own `func load(path)`. `preload` and `print` are already
        # dropped at the call site; listed for symmetry.
        "load", "preload", "print",
    }),
    # Godot's own brand blue.
    color_hex="#478CBF",
)

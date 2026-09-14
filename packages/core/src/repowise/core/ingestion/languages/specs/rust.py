"""LanguageSpec for rust (extracted from the registry data table)."""

from ..spec import LanguageSpec

# Primitives plus the prelude names a file may use without importing them. Read
# by two spec fields below, which ask different questions of the same list.
_PRELUDE_TYPES = frozenset(
    {
        "bool", "char", "str", "u8", "u16", "u32", "u64", "u128", "usize",
        "i8", "i16", "i32", "i64", "i128", "isize", "f32", "f64",
        "String", "Vec", "Option", "Result", "Box", "Arc", "Rc",
        "HashMap", "HashSet", "BTreeMap", "BTreeSet", "Cow",
        "Pin", "Future", "Send", "Sync", "Sized", "Copy", "Clone",
        "Debug", "Display", "Default", "Iterator", "IntoIterator",
        "From", "Into", "TryFrom", "TryInto", "AsRef", "AsMut",
        "Fn", "FnMut", "FnOnce", "Drop", "Deref", "DerefMut",
        "Self", "self",
    }
)

SPEC = LanguageSpec(
    tag="rust",
    display_name="Rust",
    import_support="full",
    entry_stems=("mod",),
    # Cargo conventions: src/bin/ holds extra binaries; workspace binary
    # crates are conventionally named <project>-cli (typst-cli, …).
    layer_dir_hints=(("src/bin", "CLI"), ("-cli", "CLI")),
    extensions=frozenset({".rs"}),
    grammar_package="tree_sitter_rust",
    scm_file="rust.scm",
    heritage_node_types=frozenset({"impl_item", "trait_item", "struct_item", "enum_item", "function_item"}),
    entry_point_patterns=("main.rs", "lib.rs"),
    manifest_files=("Cargo.toml",),
    lock_files=("Cargo.lock",),
    generated_suffixes=(".generated.rs", ".pb.rs", ".flatbuffers_generated.rs"),
    blocked_dirs=("target",),
    builtin_calls=frozenset(
        {
            "println",
            "eprintln",
            "print",
            "eprint",
            "format",
            "format_args",
            "vec",
            "panic",
            "todo",
            "unimplemented",
            "unreachable",
            "assert",
            "assert_eq",
            "assert_ne",
            "debug_assert",
            "debug_assert_eq",
            "debug_assert_ne",
            "cfg",
            "include",
            "include_str",
            "include_bytes",
            "env",
            "option_env",
            "concat",
            "stringify",
            "line",
            "column",
            "file",
            "write",
            "writeln",
        }
    ),
    builtin_parents=frozenset(
        {
            "Error",
            "Display",
            "Debug",
            "Clone",
            "Copy",
            "Default",
            "PartialEq",
            "Eq",
            "PartialOrd",
            "Ord",
            "Hash",
            "Send",
            "Sync",
            "Sized",
            "Unpin",
            "Iterator",
            "IntoIterator",
            "From",
            "Into",
            "TryFrom",
            "TryInto",
            "AsRef",
            "AsMut",
            "Borrow",
            "BorrowMut",
            "Drop",
            "Deref",
            "DerefMut",
            "Add",
            "Sub",
            "Mul",
            "Div",
            "Rem",
            "Neg",
            "Fn",
            "FnMut",
            "FnOnce",
        }
    ),
    # Primitives plus the prelude names a file may use without importing them,
    # so a type reference to one can never point at a file this repo declares.
    # Wider than ``builtin_parents``: that set only has to cover what may sit in
    # an impl clause, this one every position a type may be written in.
    builtin_types=_PRELUDE_TYPES,
    # The same names, read for a different question: may a repo symbol answer a
    # call written ON one of these. Measured on bevy at 21 wrong of 28 hand-read
    # (M64); the seven correct ones are all a file that rebound the name from a
    # workspace crate, which the resolver exempts by reading the import list.
    external_receiver_types=_PRELUDE_TYPES,
    # Prelude constructors and std trait methods. Every one is in scope in
    # every file without an import, so a bare-name guess that answers one with
    # a repo symbol is answering for the standard library — `Ok(())` bound to
    # the single repo symbol spelled `Ok`, `.unwrap()` on a chained receiver
    # bound to an unrelated private helper. Names a repository plausibly
    # declares and calls in its own right (`new`, `from`, `get`, `len`, `map`,
    # `next`, `insert`, `push`, `parse`) are deliberately absent: the tier this
    # feeds is a guess, but it is not always a wrong one, and the gate is that
    # every edge it loses was wrong.
    builtin_methods=frozenset(
        {
            # Prelude constructors and variants
            "Ok", "Err", "Some", "None",
            # Option / Result
            "unwrap", "unwrap_or", "unwrap_or_else", "unwrap_or_default",
            "unwrap_err", "expect", "expect_err",
            "is_some", "is_none", "is_ok", "is_err",
            "ok_or", "ok_or_else", "map_err", "and_then", "or_else",
            # Iterator
            "collect", "filter_map", "flat_map", "enumerate", "rev",
            "cloned", "copied", "into_iter", "peekable", "zip",
            # Conversion and ownership
            "to_owned", "to_string", "to_vec", "as_ref", "as_mut",
            "as_str", "as_bytes", "as_slice", "borrow_mut",
            # str / slice / Path methods with no plausible repo twin
            "starts_with", "ends_with", "to_lowercase", "to_uppercase",
            "canonicalize", "is_file", "is_dir",
        }
    ),
    color_hex="#DEA584",
)

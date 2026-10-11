"""LanguageSpec for F#.

Grammar: tree-sitter-fsharp (PyPI ``tree-sitter-fsharp``, import name
``tree_sitter_fsharp``), the ionide project's grammar.

The wheel ships two grammars: ``language()`` for ``.fs``/``.fsx`` and
``language_signature()`` for ``.fsi``. ``grammar_loader`` is chosen per
language tag, not per extension, and the implementation grammar hits ERROR
recovery on a signature file's ``val`` and member-signature bodies, so
``.fsi`` keeps the regex import tier instead of an unreliable tree; see
the ``signature_file`` branch in ``parser.py``.

``heritage_node_types`` is ``anon_type_defn`` alone because that is the only
type shape that can carry ``inherit`` or ``interface ... with``; records,
unions, enums and delegates have neither. Unlike Pascal, the node carrying
the heritage is the same node the query captures, so no one-level-deeper
walk is needed on the heritage side.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="fsharp",
    display_name="F#",
    extensions=frozenset({".fs", ".fsi", ".fsx"}),
    # .NET convention shared with C#: Program.fs.
    entry_point_patterns=("Program.fs",),
    grammar_package="tree_sitter_fsharp",
    scm_file="fsharp.scm",
    heritage_node_types=frozenset({"anon_type_defn"}),
    # AST symbols and calls now, but ``open`` still resolves through the
    # declared-name regex index in resolvers/fsharp.py plus the fsproj
    # compile-order spine, so the resolver's reach is unchanged, only the
    # accuracy of what reaches it.
    import_support="partial",
    # FSharp.Core names in scope in every file without an ``open``. Two
    # groups: the union cases F# expressions are full of (``Some x`` and
    # ``Ok v`` are applications, syntactically identical to a call), and the
    # Operators functions whose names mean the same thing everywhere. This
    # set is matched receiver-blind and deletes the call site outright, so
    # ordinary library functions a project could plausibly own stay out.
    builtin_calls=frozenset({
        "Some", "None", "Ok", "Error",
        "printf", "printfn", "sprintf", "eprintf", "eprintfn", "failwithf",
        "failwith", "raise", "reraise", "invalidArg", "invalidOp", "nullArg",
        "ignore", "id", "not", "ref", "fst", "snd",
        "nameof", "typeof", "typedefof", "sizeof", "defaultArg",
        "box", "unbox", "isNull", "incr", "decr", "max", "min", "abs",
        "lock", "async", "seq", "task", "compare", "hash",
        "string", "int", "float", "bool", "char", "byte", "sbyte",
        "int16", "uint16", "int32", "uint32", "int64", "uint64",
        "decimal", "single", "double", "enum",
        # Reserved words. None of these can name a function, so a capture of
        # one is always grammar recovery inventing an application out of a
        # construct it could not parse, and the offside rule makes that a real
        # possibility rather than a theoretical one.
        "let", "member", "static", "abstract", "override", "default",
        "if", "then", "else", "elif", "match", "when", "with", "and",
        "rec", "mutable", "new", "val", "do", "in", "of", "type",
        "module", "namespace", "open", "inherit", "interface", "use",
        "yield", "return", "begin", "end", "try", "finally", "while",
        "for", "to", "downto", "function", "fun", "base", "null",
        "extern", "inline", "private", "public", "internal", "struct",
        "class", "assert", "lazy", "upcast", "downcast", "as",
    }),
    # .NET roots plus the interfaces every class implements and no repo
    # declares, the same filter C# applies to the same names.
    builtin_parents=frozenset({
        "Object", "ValueType", "Enum", "Delegate", "Attribute", "Array",
        "Exception", "SystemException", "ApplicationException", "EventArgs",
        "MarshalByRefObject",
        "IDisposable", "IEnumerable", "IEnumerator", "IComparable",
        "IEquatable", "ICloneable", "IFormattable", "ISerializable",
    }),
    # Primitives and the FSharp.Core type constructors. None of these ever
    # has an in-repo declaration, so resolving one as a type reference is a
    # guaranteed miss. Read back through is_resolvable_type_name.
    builtin_types=frozenset({
        "int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16",
        "uint32", "uint64", "nativeint", "unativeint", "byte", "sbyte",
        "float", "float32", "double", "single", "decimal", "bigint",
        "bool", "char", "string", "unit", "obj", "exn", "void",
        "list", "array", "option", "voption", "seq", "Result", "Choice",
        "Map", "Set", "Async", "Task", "Lazy", "Nullable",
        "Object", "String", "Boolean", "Int32", "Int64", "Double", "Single",
    }),
)

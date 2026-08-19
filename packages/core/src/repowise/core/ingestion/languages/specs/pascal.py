"""LanguageSpec for Object Pascal (Delphi / Free Pascal).

Covers classic Object Pascal source: Delphi (.pas/.dpr/.dpk) and Free Pascal
(.pas/.pp/.lpr). Grammar: tree-sitter-pascal (PyPI: tree-sitter-pascal,
import name tree_sitter_pascal), maintained by Jim McKeeth
(https://github.com/jimmckeeth/tree-sitter-pascal), itself descended from
Isopod/tree-sitter-pascal. AGPL-3.0, same license family as repowise itself.

heritage_node_types / parent_class_types are both "declType", not the more
obvious "declClass"/"declIntf"/"declHelper". Pascal nests the actual class
body one level inside a `Name = <class|interface|helper body>;` wrapper
(`declType`), and only the wrapper carries a `name` field -- `declClass`
itself has none. `ASTParser._find_parent` looks up
`ancestor.child_by_field_name("name")` on whatever ancestor type matches
`parent_class_types`, so pointing it at "declClass" would silently return
no parent name for every method. See extractors/heritage/pascal.py for the
matching one-level-deeper walk on the heritage side.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="pascal",
    display_name="Object Pascal",
    # ``.inc`` is a Delphi include convention, but it is also C++'s and PHP's,
    # and this spec held it exclusively. The corpus has 24 C++ ``.inc`` files
    # and zero Pascal ones, so it now belongs to cpp — see the note on
    # ``specs/cpp.INCLUDE_FRAGMENT_EXTENSIONS``. Dropped rather than left to
    # first-spec-wins ordering so the routing survives a reordering of
    # ``ALL_SPECS``.
    extensions=frozenset({".pas", ".pp", ".dpr", ".dpk", ".lpr"}),
    grammar_package="tree_sitter_pascal",
    scm_file="pascal.scm",
    heritage_node_types=frozenset({"declType"}),
    # No dedicated resolver shipped in this first cut (see PR description) --
    # imports still resolve via the generic stem-map fallback, which should
    # cover the near-universal `unit name == file stem` Delphi/FPC convention.
    import_support="none",
    # Delphi project (.dpr) / package (.dpk) files and FPC/Lazarus project
    # (.lpr) are the closest equivalent to a language "main" -- each contains
    # exactly one top-level `program`/`library`/`package` block.
    entry_point_patterns=("*.dpr", "*.dpk", "*.lpr"),
    # No de-facto single manifest file across the ecosystem: Delphi project
    # metadata lives in .dproj (MSBuild XML, not Pascal source), FPC/Lazarus
    # in .lpi (also XML). Neither is source, so left empty rather than wired
    # to a non-Pascal parser.
    manifest_files=(),
    shebang_tokens=(),
    # Common RTL calls worth excluding from the project call graph, spelled
    # the way the RTL itself declares them (Pascal is case-insensitive, but
    # `ASTParser._extract_calls` does a case-sensitive `target_name in
    # _call_builtins` check against the raw source text, so entries need to
    # match real-world casing, not lowercased). This list is intentionally
    # conservative -- System-unit built-ins only; framework calls (VCL/FMX/
    # LCL) are ordinary project dependencies and should still show up as
    # graph edges.
    builtin_calls=frozenset({
        "WriteLn", "Write", "ReadLn", "Read",
        "Length", "SetLength", "High", "Low", "Ord", "Chr", "Succ", "Pred",
        "Inc", "Dec", "Copy", "Pos", "Concat", "Trim", "TrimLeft", "TrimRight",
        "Format", "IntToStr", "StrToInt", "StrToIntDef", "FloatToStr",
        "Assigned", "Exit",
        "New", "Dispose", "GetMem", "FreeMem", "ReallocMem",
        "FreeAndNil", "Assert", "Supports", "TypeInfo",
    }),
    # Universal Delphi/FPC root types -- filtered the same way "object" is
    # filtered from Python heritage or "NSObject" from Swift.
    builtin_parents=frozenset({
        "TObject", "TInterfacedObject", "TPersistent", "TComponent",
        "IUnknown", "IInterface", "IDispatch",
    }),
    color_hex="#E3352E",
)

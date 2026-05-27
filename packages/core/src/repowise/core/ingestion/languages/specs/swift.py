"""LanguageSpec for swift (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="swift",
    display_name="Swift",
    extensions=frozenset({".swift"}),
    grammar_package="tree_sitter_swift",
    scm_file="swift.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "protocol_declaration", "extension_declaration"}
    ),
    manifest_files=("Package.swift",),
    builtin_calls=frozenset(
        {
            "print",
            "debugPrint",
            "fatalError",
            "precondition",
            "assert",
            "min",
            "max",
            "abs",
            "stride",
            "zip",
            "map",
            "filter",
            "reduce",
            "sorted",
        }
    ),
    builtin_parents=frozenset(
        {
            "NSObject",
            "Codable",
            "Encodable",
            "Decodable",
            "Hashable",
            "Equatable",
            "Comparable",
            "CustomStringConvertible",
            "Error",
            "Sendable",
        }
    ),
    color_hex="#F05138",
)

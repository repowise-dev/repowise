"""LanguageSpec for swift (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="swift",
    display_name="Swift",
    # Live-validated on Alamofire @ 7595cbc: intra-module type edges,
    # SPM target mapping, @main entry detection — 0% orphans, 0.70 resolution.
    import_support="full",
    # XCTest/SPM conventions: FooTest(s).swift; Tests/ root is a generic token.
    test_camel_suffixes=("Test", "Tests"),
    entry_point_patterns=("main.swift", "App.swift"),
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

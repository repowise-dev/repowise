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
    # Standard-library and Foundation type names, read only by the bare-name
    # fallback: an `extension Data` adds members to Foundation's type but does
    # not own the initializer `Data(contentsOf:)` names.
    #
    # Rust's rule, a name a repository plausibly declares itself stays off, is
    # why `Result`, `Error`, `Task`, `Operation`, `Request` and `Response` are
    # absent. `OperationQueue` came off on measurement: Alamofire declares a
    # real `convenience init` on it and the call site invokes that one. A name
    # match cannot see that, which is this guard's ceiling.
    builtin_methods=frozenset(
        {
            # Foundation value types
            "Data", "URL", "URLRequest", "URLResponse", "HTTPURLResponse",
            "URLComponents", "URLQueryItem", "URLSession", "URLSessionConfiguration",
            "UUID", "Date", "DateComponents", "DateFormatter", "ISO8601DateFormatter",
            "NumberFormatter", "IndexPath", "IndexSet", "CharacterSet",
            "Bundle", "Locale", "TimeZone", "Calendar", "FileManager",
            "JSONDecoder", "JSONEncoder", "JSONSerialization",
            "PropertyListDecoder", "PropertyListEncoder",
            "NotificationCenter", "ProcessInfo", "RunLoop", "Thread",
            "DispatchQueue", "DispatchGroup", "DispatchSemaphore", "DispatchTime",
            "InputStream", "OutputStream", "Pipe",
            "NSNumber", "NSString", "NSError", "NSNull", "NSLock", "NSRecursiveLock",
            # Stdlib containers and scalars
            "Array", "Dictionary", "Set", "String", "Substring", "Character",
            "Int", "Int8", "Int16", "Int32", "Int64",
            "UInt", "UInt8", "UInt16", "UInt32", "UInt64",
            "Double", "Float", "Bool", "Range", "ClosedRange",
        }
    ),
    color_hex="#F05138",
)

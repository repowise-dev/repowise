"""LanguageSpec for vbnet."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="vbnet",
    display_name="VB.NET",
    import_support="full",
    # Same MSTest/NUnit/xUnit conventions as C# — VB.NET shares the .NET
    # test-runner ecosystem, not a distinct one.
    test_camel_suffixes=("Test", "Tests", "Spec", "Specs"),
    test_dir_suffixes=(".Tests", ".Specs"),
    layer_dir_hints=((".Api", "API"), (".Domain", "Service"), (".Infrastructure", "Data")),
    extensions=frozenset({".vb"}),
    grammar_package="tree_sitter_vbnet",
    scm_file="vbnet.scm",
    heritage_node_types=frozenset(
        {
            "class_block",
            "interface_block",
            "structure_block",
            "module_block",
        }
    ),
    entry_point_patterns=(
        "Program.vb",
        "Startup.vb",
        "ApplicationEvents.vb",  # WinForms/WPF "My Project" application framework
    ),
    manifest_files=(
        "Directory.Build.props",
        "Directory.Build.targets",
        "Directory.Packages.props",
        "global.json",
        "nuget.config",
        "NuGet.Config",
    ),
    lock_files=("packages.lock.json",),
    generated_suffixes=(
        ".g.vb",
        ".Designer.vb",
        ".AssemblyInfo.vb",
        ".AssemblyAttributes.vb",
        ".g.i.vb",
    ),
    blocked_dirs=("bin", "obj", ".vs", "TestResults", "packages"),
    builtin_calls=frozenset(
        {
            "Console",
            "Math",
            "Convert",
            "String",
            "Object",
            "Array",
            "GC",
            "Environment",
            "Activator",
            "Task",
            "GetType",
            "NameOf",
            "CType",
            "DirectCast",
            "TryCast",
            "CStr",
            "CInt",
            "CDbl",
            "CBool",
            "CDate",
            "CObj",
            "CLng",
            "CShort",
            "CByte",
            "CSng",
            "CDec",
            "CChar",
            "IIf",
            "IsNothing",
            "IsDBNull",
        }
    ),
    builtin_parents=frozenset(
        {
            "Object",
            "ValueType",
            "Enum",
            "Exception",
            "SystemException",
            "ApplicationException",
            "IDisposable",
            "IEnumerable",
            "IEnumerator",
            "IComparable",
            "ICloneable",
            "IEquatable",
        }
    ),
    color_hex="#945db7",
)

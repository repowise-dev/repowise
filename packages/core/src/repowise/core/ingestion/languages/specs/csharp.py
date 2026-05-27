"""LanguageSpec for csharp (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="csharp",
    display_name="C#",
    extensions=frozenset({".cs"}),
    grammar_package="tree_sitter_c_sharp",
    scm_file="csharp.scm",
    heritage_node_types=frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "record_declaration",
        }
    ),
    entry_point_patterns=(
        "Program.cs",
        "Startup.cs",
        "MauiProgram.cs",  # .NET MAUI host bootstrap
        "Main.cs",  # Tizen / classic console entry
        "App.xaml.cs",  # WPF / WinUI / MAUI app shell
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
        ".g.cs",
        ".Designer.cs",
        ".AssemblyInfo.cs",
        ".AssemblyAttributes.cs",
        ".g.i.cs",
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
            "Interlocked",
            "nameof",
            "typeof",
            "sizeof",
            "default",
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
    color_hex="#178600",
)

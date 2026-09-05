"""LanguageSpec for vbnet (VB.NET) at the Good tier."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="vbnet",
    display_name="VB.NET",
    import_support="full",
    # VB.NET tests follow the MSTest/xUnit conventions (FooTests/FooTest),
    # mostly as sibling files in the same project.
    test_camel_suffixes=("Test", "Tests", "Spec", "Specs"),
    test_dir_suffixes=(".Tests", ".Specs"),
    extensions=frozenset({".vb"}),
    grammar_package="tree_sitter_vb_dotnet",
    scm_file="vbnet.scm",
    heritage_node_types=frozenset(
        {
            "class_block",
            "interface_block",
            "structure_block",
        }
    ),
    manifest_files=(
        "Directory.Build.props",
        "Directory.Build.targets",
        "Directory.Packages.props",
        "global.json",
        "nuget.config",
        "NuGet.Config",
    ),
    build_config_manifests=(
        "Directory.Build.props",
        "Directory.Build.targets",
        "Directory.Packages.props",
        "global.json",
        "nuget.config",
        "NuGet.Config",
    ),
    lock_files=("packages.lock.json",),
    generated_suffixes=(
        ".designer.vb",
        ".assemblyinfo.vb",
        ".g.vb",
    ),
    blocked_dirs=("bin", "obj", ".vs", "TestResults", "packages"),
    builtin_calls=frozenset(
        {
            "Console",
            "Math",
            "Convert",
            "String",
            "Object",
            "GC",
            "Environment",
            "Activator",
            "Task",
            "Interlocked",
        }
    ),
    builtin_parents=frozenset(
        {
            # The C# set, which VB.NET inherits verbatim: same BCL, same names.
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
            # UI and component base classes. VB.NET estates are WinForms-heavy
            # and every designer form writes ``Inherits Form``, which would
            # otherwise bind to any repo class of the same name.
            "Form",
            "Control",
            "UserControl",
            "Component",
            "Page",
            "Window",
            "Attribute",
            "EventArgs",
            "MarshalByRefObject",
            "INotifyPropertyChanged",
            "IComparer",
            "ICollection",
            "IList",
            "IDictionary",
        }
    ),
    builtin_types=frozenset(
        {
            # Built-in value/reference keywords
            "Boolean",
            "Byte",
            "Short",
            "Integer",
            "Long",
            "Single",
            "Double",
            "Decimal",
            "Char",
            "String",
            "Object",
            "Date",
            "Nothing",
            # Common BCL types that are always external
            "Task",
            "CancellationToken",
            "Action",
            "Func",
            "Type",
            "Exception",
            "DateTime",
            "DateTimeOffset",
            "TimeSpan",
            "Guid",
            "Uri",
            "Stream",
        }
    ),
    color_hex="#945DB7",
)

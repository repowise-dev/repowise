"""LanguageSpec for scala (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="scala",
    display_name="Scala",
    extensions=frozenset({".scala"}),
    grammar_package="tree_sitter_scala",
    scm_file="scala.scm",
    heritage_node_types=frozenset({"class_definition", "trait_definition", "object_definition"}),
    manifest_files=("build.sbt",),
    builtin_calls=frozenset(
        {
            "println",
            "print",
            "require",
            "assert",
            "Some",
            "None",
            "Left",
            "Right",
            "Nil",
            "List",
            "Map",
            "Set",
            "Vector",
            "Array",
        }
    ),
    builtin_parents=frozenset(
        {
            "Any",
            "AnyRef",
            "AnyVal",
            "Product",
            "Serializable",
            "Throwable",
            "Exception",
            "RuntimeException",
            "Ordered",
            "Ordering",
        }
    ),
    color_hex="#DC322F",
)

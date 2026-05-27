"""LanguageSpec for clojure (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="clojure",
    display_name="Clojure",
    extensions=frozenset({".clj", ".cljs", ".cljc"}),
    is_passthrough=True,
)

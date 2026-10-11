"""LanguageSpec for clojure (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="clojure",
    display_name="Clojure",
    extensions=frozenset({".clj", ".cljs", ".cljc"}),
    # Leiningen/deps.edn convention: src/<app>/core.clj holds -main.
    entry_point_patterns=("core.clj", "main.clj"),
    manifest_files=("deps.edn", "project.clj"),
    is_passthrough=True,
    # Lightweight regex resolver: ns :require/:use forms → (ns …) index.
    import_support="partial",
)

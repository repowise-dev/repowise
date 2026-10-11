"""LanguageSpec for erlang (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="erlang",
    display_name="Erlang",
    extensions=frozenset({".erl", ".hrl"}),
    # OTP application callback modules: <name>_app.erl.
    entry_point_patterns=("*_app.erl",),
    manifest_files=("rebar.config",),
    is_passthrough=True,
    # Lightweight regex resolver: -include(_lib)/-behaviour + qualified calls
    # against the -module() index.
    import_support="partial",
)

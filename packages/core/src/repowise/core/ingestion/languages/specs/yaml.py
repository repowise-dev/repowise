"""LanguageSpec for yaml (extracted from the registry data table)."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="yaml",
    display_name="YAML",
    extensions=frozenset({".yaml", ".yml"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#CB171E",
    # Ansible playbook/role wiring via the lightweight tier: real edges
    # inside Ansible projects, nothing claimed for plain YAML configs.
    import_support="partial",
)

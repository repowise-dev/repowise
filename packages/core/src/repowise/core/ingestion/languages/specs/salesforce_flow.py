"""LanguageSpec for Salesforce flows.

Claims ``*.flow-meta.xml``, and a legacy ``flows/<ApiName>-<version>.flow`` by its
directory, since ``.flow`` alone is also Flow's (the JS type checker) ``*.js.flow``.
``is_code=False``: the org places every flow, so dead code never reports one.
"""

from pathlib import PurePosixPath

from ..spec import LanguageSpec


def is_legacy_flow_path(path: str) -> bool:
    """True for a metadata API ``flows/<Name>-<version>.flow`` file."""
    p = PurePosixPath(path.replace("\\", "/"))
    # A flow API name has no dot, so ``flows/types.js.flow`` stays JavaScript's.
    return p.suffix.lower() == ".flow" and p.parent.name == "flows" and "." not in p.stem


SPEC = LanguageSpec(
    tag="salesforce_flow",
    display_name="Salesforce Flow",
    name_suffixes=frozenset({".flow-meta.xml"}),
    is_code=False,
    is_passthrough=True,
    color_hex="#00A1E0",
)

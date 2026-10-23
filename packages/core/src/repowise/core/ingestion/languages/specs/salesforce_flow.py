"""LanguageSpec for Salesforce flows.

Source format keeps one flow per ``flows/<ApiName>.flow-meta.xml``; the legacy
metadata API format writes ``flows/<ApiName>-<version>.flow``. ``.flow`` alone
is not claimed by extension because Flow (the JavaScript type checker) ships
``*.js.flow`` declaration files: a legacy flow routes here by its ``flows/``
directory instead.

``special_handlers`` reads each file into one function-kind symbol named by the
flow's API name; ``framework_edges/salesforce_metadata.py`` links it to the Apex
actions, subflows and screen components it names. ``is_code=False`` because
the flow itself is placed by the org (a record trigger, a schedule, an App
Builder page, a user), so dead code never reports one.
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

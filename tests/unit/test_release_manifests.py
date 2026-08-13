"""The manifests a release has to bump stay in step with the package version.

``server.json`` is the MCP registry manifest, and it sat at 0.17.1 against a
0.41.0 package for roughly twenty-four releases. The cause was not carelessness:
nothing in the release process named it, so there was no moment at which anyone
was supposed to notice. This test is the half of the fix that does not depend on
anyone reading a checklist. The files below are the complete set that must move
with ``pyproject.toml``, so a red run here names exactly what to bump.

Deliberately out of scope:

* ``plugins/codex/.codex-plugin/plugin.json`` rides its own cadence by decision,
  bumped only in releases where the Codex plugin shipped changes, so it is
  correct for it to differ here.
* ``packages/vscode/package.json`` is an independent version line with its own
  tag namespace.

``server.json`` also has to stay factually true about the CLI it describes, not
just current. It told installers the repository path was required while
``repowise mcp`` has accepted no argument since long before, so the registry
published a stricter invocation than the ``.mcp.json`` we generate, the README
and both plugins. A version bump would not have caught that.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _read_json(relpath: str) -> dict:
    return json.loads((ROOT / relpath).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def package_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _versions_under_test() -> list[tuple[str, str]]:
    """(label, dotted lookup) for every manifest that tracks the package."""
    return [
        ("server.json", "version"),
        ("server.json", "packages.0.version"),
        (".claude-plugin/marketplace.json", "plugins.0.version"),
        ("plugins/claude-code/.claude-plugin/plugin.json", "version"),
    ]


def _dig(data: object, dotted: str) -> object:
    for part in dotted.split("."):
        data = data[int(part)] if part.isdigit() else data[part]  # type: ignore[index]
    return data


@pytest.mark.parametrize(("relpath", "dotted"), _versions_under_test())
def test_a_release_manifest_tracks_the_package_version(
    relpath: str, dotted: str, package_version: str
) -> None:
    found = _dig(_read_json(relpath), dotted)
    assert found == package_version, (
        f"{relpath} {dotted} is {found!r}, pyproject.toml is {package_version!r}. "
        "Every file this module names has to move together in a release commit."
    )


@pytest.mark.parametrize(
    "module_path",
    [
        "packages/cli/src/repowise/cli/__init__.py",
        "packages/core/src/repowise/core/__init__.py",
        "packages/server/src/repowise/server/__init__.py",
    ],
)
def test_a_package_dunder_version_tracks_pyproject(
    module_path: str, package_version: str
) -> None:
    text = (ROOT / module_path).read_text(encoding="utf-8")
    assert f'__version__ = "{package_version}"' in text, (
        f"{module_path} does not carry __version__ = {package_version!r}"
    )


def test_the_registry_manifest_describes_the_cli_it_ships() -> None:
    """Every argument ``server.json`` publishes matches ``repowise mcp``.

    An installer reading the registry gets this manifest and nothing else, so a
    stale claim here is not a doc bug, it is a wrong install command.
    """
    from repowise.cli.commands.mcp_cmd import mcp_command

    params = {param.name: param for param in mcp_command.params}
    arguments = _dig(_read_json("server.json"), "packages.0.packageArguments")
    assert isinstance(arguments, list)

    subcommand = arguments[0]
    assert subcommand["value"] == mcp_command.name, (
        "server.json invokes a different subcommand than the CLI registers"
    )

    repo_path = next(arg for arg in arguments if arg.get("valueHint") == "repo_path")
    assert repo_path["isRequired"] == params["path"].required, (
        "server.json and the CLI disagree on whether the repository path is required"
    )

    transport = next(arg for arg in arguments if arg.get("name") == "--transport")
    assert transport["value"] == params["transport"].default, (
        "server.json pins a --transport value the CLI does not default to"
    )
    assert transport["value"] in params["transport"].type.choices

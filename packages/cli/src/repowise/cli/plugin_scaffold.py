"""Templates and file writing for ``repowise plugins new <seam>``.

Each seam's module template is the matching example in
``docs/reference/PLUGINS_REFERENCE.md`` with the plugin name as ``${plugin}``. A test
renders every template with the name the doc uses and compares it to the doc's
code block, so change the two together.

AGENTS.md carries the seam's contract as the installed repowise defines it:
the "The contract:" notes of the seam module's docstring and the source of the
types a plugin implements or returns, read at scaffold time. An agent then
never depends on a doc page that may describe another version.
"""

from __future__ import annotations

import importlib
import inspect
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from string import Template

#: The plugin reference (AGENTS.md) and guide (README) at the tag of the
#: installed version; file roots only, since sections may move between files.
DOCS_URL = (
    "https://github.com/repowise-dev/repowise/blob/v${version}/docs/reference/PLUGINS_REFERENCE.md"
)
GUIDE_URL = "https://github.com/repowise-dev/repowise/blob/v${version}/docs/reference/PLUGINS.md"

_ISSUE_SOURCE = """\
import json
import urllib.error
import urllib.request

from repowise.core.issues import (
    IssueLink, SourceRefused, SourceUnavailable, issue_sources, tracker_keys,
)
from repowise.core.settings.plugins import plugin_setting, register_settings

URL = plugin_setting("${plugin}", "url", "str", None, "Tracker base URL.")
TOKEN = plugin_setting("${plugin}", "token", "str", None, "Tracker API token.", secret=True)
TYPES = {"Bug": "bug", "Incident": "incident", "Story": "feature", "Task": "task"}


class TrackerIssues:
    name = "${plugin}"
    settings = (URL, TOKEN)

    def fetch(self, request):
        base, token = request.settings[URL.key], request.credentials[TOKEN.key]
        for commit in request.commits:
            for key in tracker_keys(f"{commit.subject}\\n{commit.body}"):
                issue = self._issue(base, key, token)
                if issue is not None:
                    yield IssueLink(
                        key=key, type=TYPES.get(issue["type"], "other"), sha=commit.sha,
                        status=issue["status"], title=issue["title"],
                        url=f"{base}/browse/{key}", basis=f"message names {key}",
                    )

    def _issue(self, base, key, token):
        req = urllib.request.Request(
            f"{base}/api/issue/{key}", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (401, 403):
                raise SourceRefused("the tracker refused the token") from None
            raise SourceUnavailable(f"the tracker answered {exc.code}") from None
        except OSError:
            raise SourceUnavailable("the tracker did not answer") from None


def register():
    register_settings(URL, TOKEN)
    issue_sources.register(TrackerIssues.name, TrackerIssues())
"""

_EVIDENCE_SOURCE = """\
import json
import urllib.error
import urllib.parse
import urllib.request

from repowise.core.evidence.sources import (
    SourceBatch, SourceRefused, SourceUnavailable, evidence_sources,
)
from repowise.core.settings.plugins import plugin_setting, register_settings

URL = plugin_setting("${plugin}", "url", "str", None, "Statistics service base URL.")
TOKEN = plugin_setting("${plugin}", "token", "str", None, "Statistics API token.", secret=True)


class StatementSource:
    name = "${plugin}"
    kinds = ("statement_stats",)
    settings = (URL, TOKEN)

    def fetch(self, request):
        base, token = request.settings[URL.key], request.credentials[TOKEN.key]
        query = urllib.parse.urlencode({"after": request.cursor or ""})
        page = self._get(f"{base}/api/statements?{query}", token)
        if page["entries"]:
            yield SourceBatch(
                kind="statement_stats", workload="prod db", workload_class=None,
                commit=None, environment="production",
                entries=page["entries"], document={"window": page["window"]},
                next_cursor=page["cursor"],
            )

    def _get(self, url, token):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise SourceRefused("the service refused the token") from None
            raise SourceUnavailable(f"the service answered {exc.code}") from None
        except OSError:
            raise SourceUnavailable("the service did not answer") from None


def register():
    register_settings(URL, TOKEN)
    evidence_sources.register(StatementSource.name, StatementSource())
"""

_AUTHORIZER = """\
from repowise.server.auth import Action, authorizers


class RoleAuthorizer:
    def can_access_repo(self, principal, identifiers, action=Action.READ):
        roles = principal.claims.get("roles", ())
        if action is Action.WRITE and "writer" not in roles:
            return False
        allowed = principal.claims.get("repo_ids")
        ids = [i for i in identifiers if i]
        return allowed is None or not ids or any(i in allowed for i in ids)

    def can_use_tool(self, principal, tool, action=Action.READ):
        return action is Action.READ or "writer" in principal.claims.get("roles", ())


def register():
    authorizers.register("${plugin}", RoleAuthorizer())
"""

_FETCH_FINISH = """\
The source reads two settings: `${url_key}` (plain, kept in
`.repowise/config.yaml`) and `${token_env}` (a secret, read from the
environment only). It receives them as `request.settings[URL.key]` and
`request.credentials[TOKEN.key]`. Add or rename settings with
`plugin_setting(...)`, and list each one in both `settings = (...)` and
`register_settings(...)`. Remove `TOKEN` from all three places if your system
needs no token."""

# What the contract section shows for a fetching seam besides its own module.
_FETCH_CONTRACT = (
    ("repowise.core.evidence.sources", ("SourceUnavailable", "SourceRefused")),
    ("repowise.core.settings.plugins", ("plugin_setting",)),
)


@dataclass(frozen=True)
class SeamTemplate:
    """What ``plugins new`` writes for one seam."""

    default_name: str
    #: One line for the README title and the package description.
    summary: str
    module: str
    #: Markdown for AGENTS.md: what to change in the stub.
    finish: str
    #: Whether ``repowise sources sync`` runs it (it declares settings).
    fetches: bool
    #: (module, names) pairs whose source AGENTS.md shows as the contract; the
    #: first module's docstring also gives its "The contract:" notes.
    contract: tuple[tuple[str, tuple[str, ...]], ...]
    #: Markdown for AGENTS.md: the commands that show the plugin worked, after update.
    verify: str = ""
    #: Markdown bullets for AGENTS.md "Done when", after update.
    done: str = ""


SEAMS: dict[str, SeamTemplate] = {
    "issue-source": SeamTemplate(
        default_name="mytracker",
        summary="Links commits to tracker issues, so a commit tied to a bug counts as a fix",
        module=_ISSUE_SOURCE,
        finish="""\
1. Change `_issue` to call your tracker's API for one issue key, and `TYPES`
   to map the tracker's issue types onto `bug`, `incident`, `feature`, `task`
   or `other`. Only `bug` and `incident` count as fixes.
2. If your tracker does not use `ENG-42` style keys, replace `tracker_keys`
   with your own matcher (`number_refs` finds `#42`). Pass your project keys
   to `tracker_keys(text, projects=...)` to skip look-alikes.
3. Raise `SourceUnavailable` when the tracker is down or rate limits and
   `SourceRefused` when it rejects the token. Never put the token in a link
   or an exception message.

"""
        + _FETCH_FINISH,
        fetches=True,
        contract=(
            (
                "repowise.core.issues.sources",
                ("IssueSource", "IssueRequest", "IssueCommit", "IssueLink", "ISSUE_TYPES"),
            ),
            *_FETCH_CONTRACT,
        ),
        verify="""\
`repowise update` re-reads git history once the links change, so a commit
linked to a `bug` or `incident` issue counts as a fix from then on. Check one
commit your tracker links to an issue:

```bash
repowise changes show <sha>
git show --name-only --format= <sha>
repowise context <one of those files> --format json
```

`changes show` prints a `linked issues:` line with the key, type and title.
For a `bug` or `incident` issue, the file's `fix_history` lists it under
`linked_issues` and its `fix_count` includes the commit.
""",
        done="""\
- `repowise changes show <sha>` prints `linked issues:` with your issue for a
  commit the tracker links.
- For a bug, `repowise context <file> --format json` lists the issue under
  `fix_history.linked_issues`.
""",
    ),
    "evidence-source": SeamTemplate(
        default_name="statements",
        summary="Fetches production statement statistics as repowise evidence",
        module=_EVIDENCE_SOURCE,
        finish="""\
1. Change `fetch` and `_get` to read from your service. The stub expects a
   JSON page with `entries` (already in the `repowise-evidence-v1`
   `statement_stats` shape), `window` and `cursor`; map your own rows onto
   that shape here.
2. Return batches oldest first, each with the `next_cursor` that resumes
   after it. Change `kinds` and `kind` if you fetch another evidence kind.
3. Raise `SourceUnavailable` when the service is down or rate limits and
   `SourceRefused` when it rejects the token. Never put the token in a batch
   or an exception message.

"""
        + _FETCH_FINISH,
        fetches=True,
        contract=(
            (
                "repowise.core.evidence.sources",
                ("EvidenceSource", "SourceRequest", "SourceBatch"),
            ),
            *_FETCH_CONTRACT,
        ),
        verify="""\
Check that the runs were stored:

```bash
repowise evidence status --format json
```

`runs_stored` counts runs by kind (`statement_stats` for the stub) and
`workloads` has one row per workload your batches name (`prod db` for the
stub).
""",
        done="""\
- `repowise evidence status --format json` counts your kind under
  `runs_stored` and lists your workload under `workloads`.
""",
    ),
    "authorizer": SeamTemplate(
        default_name="roles",
        summary="Decides which repositories and tools each caller of `repowise serve` may use",
        module=_AUTHORIZER,
        finish="""\
1. Change `can_access_repo` and `can_use_tool` to your access rules. The stub
   reads `roles` and `repo_ids` from `principal.claims`, which your
   authenticator fills in.
2. Keep the `action` parameter on both methods: with it, repowise asks the
   authorizer on every call, including writes.
3. Select it with the environment variable
   `REPOWISE_SEAM_AUTHORIZER=${plugin}` when you start `repowise serve` or
   the MCP server.""",
        fetches=False,
        contract=(("repowise.server.auth", ("Authorizer", "Principal", "Action")),),
    ),
}

_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "${dist}"
version = "0.1.0"
description = "${summary}."
readme = "README.md"
requires-python = ">=3.11"
# PyPI keyword and GitHub topic repowise plugins share, so people can find yours.
keywords = ["repowise-plugin"]
dependencies = ["repowise>=${repowise_min}"]

[project.optional-dependencies]
test = ["pytest"]

# repowise imports ${package} and calls register() once per process.
[project.entry-points."repowise.plugins"]
${plugin} = "${package}:register"

[tool.hatch.build.targets.wheel]
packages = ["src/${package}"]

[tool.pytest.ini_options]
pythonpath = ["src"]
"""

_MODULE_HEAD = '''\
"""${summary}: the `${seam}` named "${plugin}". See AGENTS.md to finish it."""

'''

_MODULE_TAIL = """

# The oldest plugin API this plugin works with; `repowise plugins` shows the current one.
MIN_PLUGIN_API_VERSION = ${api}
"""

_TEST = """\
from repowise.core.plugin_testing import assert_plugin_passes


def test_plugin_meets_every_contract():
    assert_plugin_passes("${package}:register")
"""

_README = """\
# ${dist}

${summary}. A [repowise](https://github.com/repowise-dev/repowise) plugin for
the `${seam}` seam, registered as `${plugin}`.

## Install

```bash
pip install ${dist}
repowise plugins check
```

${configure}
How to write, test and publish a plugin is in the
[plugin guide](${guide}).
"""

_README_CONFIGURE_FETCH = """\
## Configure

In a repository repowise has indexed:

```bash
${set_url}
export ${token_env}=<token>
repowise sources sync --source ${plugin}
```

`repowise update` syncs it too once both settings have a value.
"""

_README_CONFIGURE_SEAM = """\
## Use

Start `repowise serve` or the MCP server with
`REPOWISE_SEAM_AUTHORIZER=${plugin}` in the environment.
"""

_AGENTS = """\
# AGENTS.md

This package is a repowise plugin. `src/${package}/__init__.py` registers the
`${seam}` named `${plugin}` from `register()`, which repowise calls once per
process. The stub already loads and passes the contract checks. Your job is
to make it talk to the real system.

## Finish the stub

${finish}

Keep `MIN_PLUGIN_API_VERSION` at the end of the module.

## The contract

As repowise ${version}, the version installed here, defines it in
`${contract_module}`. The plugin reference for this version, with examples,
is ${docs}.

${contract}

## Install and verify

Run these from this directory, in the environment repowise runs from:

```bash
pip install -e ".[test]"
repowise plugins check
pytest
```

`repowise plugins check` must list `pass` for `${seam}:${plugin}`. A
`fail` line names the rule that broke and how to fix it. `skipped` next to a
setting only means it has no value yet. Add
`--format json` to either `repowise plugins` command for output a script can
read.
${run}
## Done when

- `repowise plugins check` exits 0.
- `pytest` passes.
${done}"""

_AGENTS_RUN_FETCH = """
## Configure and run

Run these in a repository repowise has indexed (`repowise init` first if it
has no `.repowise` folder):

```bash
${set_url}
export ${token_env}=<token>
repowise sources sync --source ${plugin}
repowise update
```

The token is read from the environment on every run and never written to a
file. `repowise plugins` lists each setting that still has no value, with the
command that sets it. `repowise sources sync` prints, for each source, what
it stored or why not, and the command to run next. `repowise update` runs the
same sync before it updates the index, and so does every later update.

${verify}"""

_AGENTS_DONE_FETCH = """\
- `repowise sources sync --source ${plugin}` reports `stored`.
- `repowise update` has run after it.
${seam_done}"""


def valid_name(name: str) -> bool:
    """Whether *name* is a plugin name, by the rule ``plugin_setting`` applies."""
    from repowise.core.settings.plugins import plugin_setting

    try:
        plugin_setting(name, "url", "str", None, "")
    except ValueError:
        return False
    return True


def package_name(name: str) -> str:
    return "repowise_" + name.replace("-", "_")


def dist_name(name: str) -> str:
    return "repowise-" + name.replace("_", "-")


def render_module(seam: str, plugin: str) -> str:
    """The module body for *seam*, as PLUGINS_REFERENCE.md shows it, named *plugin*."""
    return Template(SEAMS[seam].module).substitute(plugin=plugin)


def _markdown(text: str) -> str:
    """reST markup from a docstring as Markdown: roles and double backticks."""
    text = re.sub(
        r":\w+:`(~?)\.?([\w.]+)`",
        lambda m: f"`{m[2].rsplit('.', 1)[-1] if m[1] else m[2]}`",
        text,
    )
    return text.replace("``", "`")


def _contract_notes(module_name: str) -> str:
    """The "The contract:" bullets of *module_name*'s docstring, as Markdown."""
    doc = importlib.import_module(module_name).__doc__ or ""
    _, found, rest = doc.partition("The contract:\n")
    if not found:
        return ""
    lines = []
    for line in rest.strip("\n").splitlines():
        if line and not line.startswith(("* ", "  ")):
            break
        lines.append(line)
    return _markdown("\n".join(lines).strip())


def _source_of(obj: object, name: str) -> str:
    """A class's source, a function's signature and docstring, a constant's value."""
    if inspect.isclass(obj):
        return textwrap.dedent(inspect.getsource(obj)).rstrip()
    if inspect.isfunction(obj):
        signature = str(inspect.signature(obj)).replace("'", "")
        doc = textwrap.indent(f'"""{inspect.cleandoc(obj.__doc__ or "")}"""', "    ")
        return f"def {name}{signature}:\n{doc}"
    return f"{name} = {obj!r}"


def render_contract(seam: str) -> str:
    """The contract section of AGENTS.md for *seam*, read from the installed repowise."""
    contract = SEAMS[seam].contract
    parts = [_contract_notes(contract[0][0])]
    for module_name, names in contract:
        module = importlib.import_module(module_name)
        code = "\n\n\n".join(_source_of(getattr(module, n), n) for n in names)
        parts.append(f"From `{module_name}`:\n\n```python\n{code}\n```")
    return "\n\n".join(p for p in parts if p)


def _values(seam: str, plugin: str) -> dict[str, str]:
    from repowise.core import __version__
    from repowise.core.registry.plugins import PLUGIN_API_VERSION
    from repowise.core.settings.plugins import plugin_setting
    from repowise.core.settings.views import set_command

    spec = SEAMS[seam]
    major, minor = __version__.split(".")[:2]
    url = plugin_setting(plugin, "url", "str", None, "")
    token = plugin_setting(plugin, "token", "str", None, "", secret=True)
    values = {
        "seam": seam,
        "plugin": plugin,
        "package": package_name(plugin),
        "dist": dist_name(plugin),
        "summary": spec.summary,
        "version": __version__,
        "docs": Template(DOCS_URL).substitute(version=__version__),
        "guide": Template(GUIDE_URL).substitute(version=__version__),
        "contract_module": spec.contract[0][0],
        "contract": render_contract(seam),
        "api": str(PLUGIN_API_VERSION),
        "repowise_min": f"{major}.{minor}",
        "url_key": url.key,
        "token_env": token.key,
        "set_url": set_command(url.key, "https://example.com"),
    }
    values["finish"] = _fill(spec.finish, values)
    values["verify"] = spec.verify
    values["seam_done"] = spec.done
    values["configure"] = _fill(
        _README_CONFIGURE_FETCH if spec.fetches else _README_CONFIGURE_SEAM, values
    )
    values["run"] = _fill(_AGENTS_RUN_FETCH, values) if spec.fetches else ""
    values["done"] = _fill(_AGENTS_DONE_FETCH, values) if spec.fetches else ""
    return values


def _fill(text: str, values: dict[str, str]) -> str:
    return Template(text).substitute(values)


def scaffold(seam: str, plugin: str, dest: Path) -> list[Path]:
    """Write the plugin package for *seam* into *dest*; the files written, in order.

    Raises ``FileExistsError`` when *dest* exists and is not empty, so nothing
    a user already has is overwritten.
    """
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise FileExistsError(str(dest))
    v = _values(seam, plugin)
    module = _fill(_MODULE_HEAD, v) + render_module(seam, plugin) + _fill(_MODULE_TAIL, v)
    files = {
        "pyproject.toml": _fill(_PYPROJECT, v),
        f"src/{v['package']}/__init__.py": module,
        "tests/test_plugin.py": _fill(_TEST, v),
        "README.md": _fill(_README, v),
        "AGENTS.md": _fill(_AGENTS, v),
    }
    written = []
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def next_commands(seam: str, plugin: str, shown_dir: str) -> list[str]:
    """The three commands to run after scaffolding, in order."""
    third = (
        f"repowise sources sync --source {plugin}" if SEAMS[seam].fetches else "repowise plugins"
    )
    return [f"pip install -e {shown_dir}", "repowise plugins check", third]

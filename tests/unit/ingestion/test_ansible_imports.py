"""Unit tests for Ansible YAML dependency extraction + resolution (#4397).

Covers every construct the extractor claims (import_playbook, roles:,
import/include_tasks, import/include_role, role meta dependencies), the
FQCN spelling, block nesting, the Jinja-skip rule, and the guards that
keep plain YAML configs import-free.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.lightweight_imports import extract_lightweight_imports
from repowise.core.ingestion.lightweight_imports.ansible import extract_ansible_imports
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.ansible import resolve_ansible_import
from repowise.core.ingestion.resolvers.context import ResolverContext


def _ctx(repo: Path | None, files: dict[str, str]) -> ResolverContext:
    """ResolverContext over *files* ({path: content}); writes them under *repo*."""
    if repo is not None:
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    stem_map: dict[str, list[str]] = {}
    for p in files:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=set(files),
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo,
    )


def _file_info(rel: str, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=f"/tmp/{rel}",
        language=lang,  # type: ignore[arg-type]
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _modules(imports) -> list[str]:
    return [imp.module_path for imp in imports]


def _extract(path: str, text: str, lang: str = "yaml"):
    return extract_lightweight_imports(_file_info(path, lang), text.encode())


PLAYBOOK = """\
- import_playbook: base.yml
- name: web
  hosts: webservers
  roles:
    - common
    - role: app
      tags: [deploy]
  tasks:
    - import_tasks: tasks/setup.yml
    - name: deploy
      block:
        - include_tasks:
            file: tasks/deploy.yml
        - ansible.builtin.include_role:
            name: monitoring
      rescue:
        - import_role:
            name: rollback
"""

ROLE_META = """\
galaxy_info:
  author: someone
dependencies:
  - base
  - role: users
"""


class TestAnsibleExtraction:
    def test_import_playbook(self) -> None:
        assert _modules(extract_ansible_imports("- import_playbook: base.yml\n")) == ["base.yml"]

    def test_fqcn_playbook_import(self) -> None:
        src = "- ansible.builtin.import_playbook: base.yml\n"
        assert _modules(extract_ansible_imports(src)) == ["base.yml"]

    def test_full_playbook(self) -> None:
        assert _modules(extract_ansible_imports(PLAYBOOK)) == [
            "base.yml",
            "role:common",
            "role:app",
            "tasks/setup.yml",
            "tasks/deploy.yml",
            "role:monitoring",
            "role:rollback",
        ]

    def test_task_file_top_level(self) -> None:
        src = "- import_tasks: sub.yml\n- include_role: cleanup\n"
        assert _modules(extract_ansible_imports(src)) == ["sub.yml", "role:cleanup"]

    def test_meta_main_dependencies(self) -> None:
        assert _modules(extract_ansible_imports(ROLE_META)) == ["role:base", "role:users"]

    def test_meta_without_galaxy_info_is_ignored(self) -> None:
        src = "dependencies:\n  - base\n"
        assert extract_ansible_imports(src) == []

    def test_jinja_targets_skipped(self) -> None:
        src = "- import_tasks: '{{ item }}.yml'\n- include_role: '{{ role_name }}'\n"
        assert extract_ansible_imports(src) == []

    def test_plain_yaml_untouched(self) -> None:
        src = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"
        assert extract_ansible_imports(src) == []

    def test_byte_gate(self) -> None:
        assert extract_ansible_imports("key: value\nlist: [1, 2]\n") == []

    def test_invalid_yaml_returns_nothing(self) -> None:
        src = "- roles: [{{{\n  unbalanced\n"
        assert extract_ansible_imports(src) == []

    def test_dispatch_through_language(self) -> None:
        assert _modules(_extract("site.yml", PLAYBOOK)) == _modules(
            extract_ansible_imports(PLAYBOOK)
        )

    def test_non_yaml_language_untouched(self) -> None:
        assert _extract("models/stg.sql", PLAYBOOK, "sql") == []


FILES = {
    "site.yml": PLAYBOOK,
    "base.yml": "- name: base\n  hosts: all\n",
    "tasks/setup.yml": "- name: setup\n  debug: msg=x\n",
    "tasks/deploy.yml": "- name: deploy\n  debug: msg=x\n",
    "roles/common/tasks/main.yml": "- name: common\n  debug: msg=x\n",
    "roles/app/tasks/main.yaml": "- name: app\n  debug: msg=x\n",
    "roles/base/tasks/main.yml": "- name: base\n  debug: msg=x\n",
    "roles/users/tasks/main.yml": "- name: users\n  debug: msg=x\n",
    "setup.yml": "- name: root\n  debug: msg=x\n",
    "sub/play.yml": "- name: sub\n  hosts: all\n  tasks:\n    - import_tasks: setup.yml\n",
    "sub/setup.yml": "- name: nested\n  debug: msg=x\n",
    "playbooks/web.yml": "- name: web\n  hosts: all\n  roles: [common]\n",
}


class TestAnsibleResolution:
    def test_playbook_import_relative(self) -> None:
        ctx = _ctx(None, FILES)
        assert resolve_import("base.yml", "site.yml", "yaml", ctx) == "base.yml"

    def test_task_file_relative(self) -> None:
        ctx = _ctx(None, FILES)
        assert resolve_import("tasks/setup.yml", "site.yml", "yaml", ctx) == "tasks/setup.yml"

    def test_importer_dir_first(self) -> None:
        ctx = _ctx(None, FILES)
        # sub/play.yml's own setup.yml wins over the repo-root one.
        assert resolve_import("setup.yml", "sub/play.yml", "yaml", ctx) == "sub/setup.yml"

    def test_repo_root_is_not_a_fallback(self) -> None:
        # Ansible resolves import_playbook from the including file; a root
        # other.yml must not stand in for a missing sub/other.yml.
        files = dict(FILES)
        files["other.yml"] = "- name: other\n  hosts: all\n"
        ctx = _ctx(None, files)
        assert resolve_import("other.yml", "sub/play.yml", "yaml", ctx) is None
        assert resolve_ansible_import("other", "sub/play.yml", ctx) is None

    def test_extension_probing(self) -> None:
        ctx = _ctx(None, FILES)
        assert resolve_ansible_import("tasks/setup", "site.yml", ctx) == "tasks/setup.yml"

    def test_role_from_root_playbook(self) -> None:
        ctx = _ctx(None, FILES)
        assert (
            resolve_import("role:common", "site.yml", "yaml", ctx) == "roles/common/tasks/main.yml"
        )

    def test_role_yaml_extension(self) -> None:
        ctx = _ctx(None, FILES)
        assert resolve_import("role:app", "site.yml", "yaml", ctx) == "roles/app/tasks/main.yaml"

    def test_role_from_subdirectory_playbook(self) -> None:
        ctx = _ctx(None, FILES)
        # playbooks/web.yml finds the repo-root roles/ tree by walking up.
        assert (
            resolve_import("role:common", "playbooks/web.yml", "yaml", ctx)
            == "roles/common/tasks/main.yml"
        )

    def test_missing_file_returns_none(self) -> None:
        ctx = _ctx(None, FILES)
        assert resolve_import("nope/missing.yml", "site.yml", "yaml", ctx) is None

    def test_galaxy_role_returns_none(self) -> None:
        ctx = _ctx(None, FILES)
        assert resolve_import("role:geerlingguy.nginx", "site.yml", "yaml", ctx) is None

    def test_galaxy_role_rejected_even_when_directory_exists(self) -> None:
        # A vendored roles/geerlingguy.nginx/ directory must not lure the
        # resolver into a wrong edge: dotted names are Galaxy-scoped.
        files = dict(FILES)
        files["roles/geerlingguy.nginx/tasks/main.yml"] = "- debug: msg=x\n"
        ctx = _ctx(None, files)
        assert resolve_import("role:geerlingguy.nginx", "site.yml", "yaml", ctx) is None

    def test_collection_role_rejected(self) -> None:
        files = dict(FILES)
        files["roles/ns.coll.role/tasks/main.yml"] = "- debug: msg=x\n"
        ctx = _ctx(None, files)
        assert resolve_import("role:ns.coll.role", "site.yml", "yaml", ctx) is None

    def test_path_like_role_rejected(self) -> None:
        files = dict(FILES)
        files["roles/vendor/common/tasks/main.yml"] = "- debug: msg=x\n"
        ctx = _ctx(None, files)
        assert resolve_import("role:vendor/common", "site.yml", "yaml", ctx) is None

    def test_empty_target_returns_none(self) -> None:
        ctx = _ctx(None, FILES)
        assert resolve_ansible_import("  ", "site.yml", ctx) is None

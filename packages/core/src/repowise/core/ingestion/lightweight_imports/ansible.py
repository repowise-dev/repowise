"""Dependency extraction for Ansible YAML: playbooks, task files, role meta.

Ansible repositories declare their wiring in YAML, which the registry classifies
as generic passthrough config, so the playbook/role layer produced no edges at
all (#4397). The constructs that carry a dependency are a small, closed set of
keys::

    - import_playbook: site.yml                 # playbook -> playbook
    - ansible.builtin.import_playbook: site.yml # FQCN spelling
    - import_tasks: tasks/foo.yml               # play/task-file -> task file
    - include_tasks: { file: tasks/foo.yml }    # dict form
    - import_role: { name: common }             # play/task -> role
    - include_role: common                      # plain-string form
    - roles: [common, { role: db }]             # play -> role
    # roles/<name>/meta/main.yml:
    dependencies: [base, { role: users }]       # role -> role

``import_*`` is static and ``include_*`` dynamic in Ansible's execution, but
both spell a dependency on a literal target, so both yield edges. Targets with
Jinja interpolation (``{{ x }}`` / ``{% x %}``) are skipped, not guessed: a
template can expand to anything, and an edge to a guessed file is worse than a
documented absence.

Ansible YAML has no file-level marker that distinguishes it from CI configs or
Kubernetes manifests, so this extractor gates twice: a byte-level substring
check before PyYAML is invoked on a file at all, then the document's own shape
(a top-level list of plays/tasks, or a role ``meta/main.yml`` carrying
``galaxy_info``). Plain YAML walks away with zero imports, the same contract
``sql`` keeps for non-dbt files.

Role references resolve by *name*, not path, so the extractor stamps them
``role:<name>`` in ``module_path`` for the resolver (``resolvers/ansible.py``);
file references stay plain relative paths. A role edge targets the role's
conventional entry file (``roles/<name>/tasks/main.yml``) -- the graph's nodes
are files, so that entry file is what stands for the role.

Known ceilings, recorded as absent edges rather than guesses:

* ``import_tasks`` with a loop or ``apply:`` is still a single literal file,
  so it is covered.
* Role ``dependencies`` count only alongside ``galaxy_info``: a bare top-level
  ``dependencies:`` list without ``galaxy_info`` is not claimed as role wiring,
  because generic config files use that key freely.
* Galaxy/collection roles and custom ``roles_path`` directories are not visible
  to a repo-local resolver; the resolver returns ``None`` for them (deferred,
  per the issue) rather than fabricating an external node.
"""

from __future__ import annotations

from ..models import Import

_FQCN = "ansible.builtin."

# Byte-level pre-gate, checked before PyYAML runs on the file at all.
_GATE_TOKENS = (
    "import_playbook",
    "import_tasks",
    "include_tasks",
    "import_role",
    "include_role",
    "roles:",
    "galaxy_info",
)

# Keys whose presence marks a top-level list item as a play, not a task.
_PLAY_KEYS = frozenset({"hosts", "roles", "tasks", "handlers", "pre_tasks", "post_tasks"})
# Play sections that hold task lists.
_TASK_LIST_KEYS = ("tasks", "handlers", "pre_tasks", "post_tasks")
# Task keys that nest further task lists.
_BLOCK_KEYS = ("block", "rescue", "always")

_ROLE_PREFIX = "role:"


def extract_ansible_imports(text: str) -> list[Import]:
    """Extract Ansible dependency references from a YAML document's text."""
    if not any(token in text for token in _GATE_TOKENS):
        return []
    try:
        import yaml  # pyyaml, already a dependency (see special_handlers)
    except ImportError:
        return []
    try:
        data = yaml.safe_load(text)
    except Exception:
        return []

    imports: list[Import] = []
    if isinstance(data, list):
        # Playbook (list of plays) or task file (list of tasks).
        for item in data:
            if not isinstance(item, dict):
                continue
            playbook = _key(item, "import_playbook")
            if isinstance(playbook, str):
                _add(imports, playbook, kind="file", raw=f"import_playbook: {playbook}")
            elif _PLAY_KEYS & item.keys():
                _walk_play(item, imports)
            else:
                _walk_task(item, imports)
    elif isinstance(data, dict):
        # Role meta/main.yml: role -> role dependencies. ``galaxy_info`` is the
        # marker that keeps a generic ``dependencies:`` list out of scope.
        dependencies = data.get("dependencies")
        if "galaxy_info" in data and isinstance(dependencies, list):
            for dep in dependencies:
                name = _role_name(dep)
                if name is not None:
                    _add(imports, name, kind="role", raw=f"dependencies: {name}")
    return imports


def _key(mapping: dict, name: str) -> object:
    """Value of *name* or its ``ansible.builtin.`` FQCN spelling, if present."""
    if name in mapping:
        return mapping[name]
    return mapping.get(_FQCN + name)


def _role_name(entry: object) -> str | None:
    """Role name from a ``roles:``/``dependencies:`` entry, or None."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        name = entry.get("role") or entry.get("name")
        if isinstance(name, str):
            return name
    return None


def _walk_play(play: dict, imports: list[Import]) -> None:
    roles = play.get("roles")
    if isinstance(roles, list):
        for entry in roles:
            name = _role_name(entry)
            if name is not None:
                _add(imports, name, kind="role", raw=f"roles: {name}")
    for key in _TASK_LIST_KEYS:
        section = play.get(key)
        if isinstance(section, list):
            for task in section:
                if isinstance(task, dict):
                    _walk_task(task, imports)


def _walk_task(task: dict, imports: list[Import]) -> None:
    for key in ("import_tasks", "include_tasks"):
        value = _key(task, key)
        target: object = value
        if isinstance(value, dict):
            target = value.get("file")
        if isinstance(target, str):
            _add(imports, target, kind="file", raw=f"{key}: {target}")
    for key in ("import_role", "include_role"):
        value = _key(task, key)
        name: object = value
        if isinstance(value, dict):
            name = value.get("name")
        if isinstance(name, str):
            _add(imports, name, kind="role", raw=f"{key}: {name}")
    for key in _BLOCK_KEYS:
        section = task.get(key)
        if isinstance(section, list):
            for sub in section:
                if isinstance(sub, dict):
                    _walk_task(sub, imports)


def _add(imports: list[Import], target: str, *, kind: str, raw: str) -> None:
    target = target.strip()
    # Jinja-interpolated targets expand at run time; skipping is documented
    # behaviour, not a miss (see module docstring).
    if not target or "{{" in target or "{%" in target:
        return
    imports.append(
        Import(
            raw_statement=raw,
            module_path=_ROLE_PREFIX + target if kind == "role" else target,
            imported_names=[],
            is_relative=True,
            resolved_file=None,
        )
    )

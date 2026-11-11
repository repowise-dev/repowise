"""Backstage-shaped catalog files: parsing, merging the two files, and check."""

from __future__ import annotations

from pathlib import Path

from repowise.core.components import check_catalog, read_catalog
from repowise.core.components.catalog import owner_group_name

_COMPONENTS = """\
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: checkout
spec:
  owner: group:default/payments
---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: payments-api
  title: Payments API
  description: Card authorisation and refunds.
  tags: [python, customer-facing]
  annotations:
    repowise.dev/paths: services/payments/**, libs/money/**
    repowise.dev/criticality: Critical
    repowise.dev/tags: pci, regulated
    repowise.dev/capability: Take payments
    backstage.io/techdocs-ref: dir:.
spec:
  type: service
  lifecycle: production
  owner: group:default/payments
  system: checkout
---
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: payments
spec:
  type: team
  profile:
    displayName: Payments Team
  children: []
  members: [alice, bob]
---
apiVersion: backstage.io/v1alpha1
kind: API
metadata:
  name: payments-rest
spec:
  type: openapi
"""


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_multi_document_file_reads_components_systems_and_groups(tmp_path):
    _write(tmp_path, ".repowise/components.yaml", _COMPONENTS)
    catalog = read_catalog(tmp_path)

    assert catalog.sources == [".repowise/components.yaml"]
    assert catalog.issues == []
    (comp,) = catalog.components
    assert comp.name == "payments-api"
    assert comp.title == "Payments API"
    assert comp.paths == ("services/payments/**", "libs/money/**")
    assert comp.criticality == "critical"
    # metadata.tags and repowise.dev/tags fold into one list, deduplicated.
    assert comp.tags == ("python", "customer-facing", "pci", "regulated")
    assert comp.capability == "Take payments"
    assert (comp.type, comp.lifecycle, comp.system) == ("service", "production", "checkout")
    assert comp.declared and comp.declared_in == ".repowise/components.yaml"
    assert [s.name for s in catalog.systems] == ["checkout"]
    (group,) = catalog.groups
    assert (group.name, group.title, group.type) == ("payments", "Payments Team", "team")
    # Membership is never read: Group carries no member field at all.
    assert not hasattr(group, "members")


def test_catalog_info_is_read_and_components_yaml_wins_on_a_duplicate(tmp_path):
    _write(tmp_path, ".repowise/components.yaml", _COMPONENTS)
    _write(
        tmp_path,
        "catalog-info.yaml",
        """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: payments-api
  annotations:
    repowise.dev/criticality: low
spec: {type: service, lifecycle: production, owner: payments}
---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: web
spec: {type: website, lifecycle: production, owner: frontend}
""",
    )
    catalog = read_catalog(tmp_path)

    assert catalog.sources == [".repowise/components.yaml", "catalog-info.yaml"]
    by_name = {c.name: c for c in catalog.components}
    assert by_name["payments-api"].criticality == "critical"
    assert by_name["web"].declared_in == "catalog-info.yaml"
    (dup,) = catalog.issues
    assert dup.severity == "warning" and dup.entity == "Component:payments-api"


def test_parse_errors_name_the_entity_and_drop_unidentifiable_ones(tmp_path):
    _write(
        tmp_path,
        "catalog-info.yaml",
        """\
apiVersion: backstage.io/v1alpha1
kind: Service
metadata: {name: odd}
---
apiVersion: backstage.io/v1beta1
kind: Component
metadata: {name: old}
spec: {type: service, lifecycle: production, owner: x}
---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: typo
  annotations:
    repowise.dev/path: src/**
    repowise.dev/criticality: urgent
    repowise.dev/tags: [pci]
spec: {type: service}
""",
    )
    catalog = read_catalog(tmp_path)
    messages = {(i.entity, i.message) for i in catalog.errors}

    assert [c.name for c in catalog.components] == ["typo"]
    assert any(e == "Service:odd" and "unknown kind" in m for e, m in messages)
    assert any(e == "Component:old" and "apiVersion" in m for e, m in messages)
    assert any("unknown annotation 'repowise.dev/path'" in m for _, m in messages)
    assert any("repowise.dev/criticality must be one of" in m for _, m in messages)
    assert any("must be a string" in m for _, m in messages)
    assert {m for e, m in messages if e == "Component:typo" and m.startswith("spec.")} == {
        "spec.lifecycle is required for a Component",
        "spec.owner is required for a Component",
    }


def test_invalid_yaml_is_one_error_not_an_exception(tmp_path):
    _write(tmp_path, "catalog-info.yaml", "kind: [unclosed\n")
    catalog = read_catalog(tmp_path)
    assert catalog.components == []
    assert catalog.errors[0].message.startswith("invalid YAML")


def test_check_reports_globs_matching_nothing_and_overlaps(tmp_path):
    _write(
        tmp_path,
        ".repowise/components.yaml",
        """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: api
  annotations: {repowise.dev/paths: "src/api/**, src/ghost/**"}
spec: {type: service, lifecycle: production, owner: platform, system: nowhere}
---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: everything
  annotations: {repowise.dev/paths: "src/**"}
spec: {type: service, lifecycle: production, owner: group:default/ghosts}
---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata: {name: pathless}
spec: {type: library, lifecycle: production, owner: platform}
---
apiVersion: backstage.io/v1alpha1
kind: System
metadata: {name: shop}
spec: {owner: platform}
---
apiVersion: backstage.io/v1alpha1
kind: Group
metadata: {name: platform}
spec: {type: team, children: []}
""",
    )
    catalog = read_catalog(tmp_path)
    issues = check_catalog(catalog, ["src/api/a.py", "src/api/b.py", "src/web/c.py"])
    by_message = {i.message: i for i in issues}

    assert by_message["glob 'src/ghost/**' matches no files"].severity == "error"
    # "api" nests wholly inside "everything": a deliberate carve-out, so a warning.
    nested = by_message[
        "2 file(s) claimed by both 'api' and 'everything' (one nests inside the other); "
        "'api' is declared first and wins"
    ]
    assert nested.severity == "warning"
    warnings = {i.message for i in issues if i.severity == "warning"}
    assert "spec.system 'nowhere' is not a declared System" in warnings
    assert "spec.owner 'group:default/ghosts' is not a declared Group" in warnings
    assert any(m.startswith("no repowise.dev/paths") for m in warnings)


def test_owner_group_name_reads_backstage_refs():
    assert owner_group_name("group:default/payments") == "payments"
    assert owner_group_name("payments") == "payments"
    assert owner_group_name("user:default/alice") is None
    assert owner_group_name(None) is None


def test_the_documented_example_parses_cleanly(tmp_path):
    example = Path(__file__).parents[3] / "examples" / "components" / "components.yaml"
    _write(tmp_path, "catalog-info.yaml", example.read_text(encoding="utf-8"))
    catalog = read_catalog(tmp_path)
    assert catalog.issues == []
    assert [c.name for c in catalog.components] == ["payments-api", "web"]


def _component(name: str, globs: str) -> str:
    return (
        "apiVersion: backstage.io/v1alpha1\nkind: Component\n"
        f"metadata:\n  name: {name}\n  annotations: {{repowise.dev/paths: \"{globs}\"}}\n"
        "spec: {type: service, lifecycle: production, owner: platform}\n"
    )


def test_a_partial_overlap_is_an_error(tmp_path):
    _write(
        tmp_path,
        "catalog-info.yaml",
        _component("left", "src/a/**, src/shared/**") + "---\n" + _component("right", "src/b/**, src/shared/**"),
    )
    files = ["src/a/x.py", "src/b/y.py", "src/shared/z.py"]
    (issue,) = [i for i in check_catalog(read_catalog(tmp_path), files) if "claimed" in i.message]
    assert issue.severity == "error"
    assert issue.message == "1 file(s) claimed by both 'left' and 'right'; 'left' is declared first and wins"


def test_globs_are_case_sensitive(tmp_path):
    _write(tmp_path, "catalog-info.yaml", _component("api", "SRC/**"))
    messages = [i.message for i in check_catalog(read_catalog(tmp_path), ["src/a.py"])]
    assert "glob 'SRC/**' matches no files" in messages


def test_an_oversized_catalog_is_refused_unparsed(tmp_path, monkeypatch):
    from repowise.core.components import catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "MAX_CATALOG_BYTES", 100)
    _write(tmp_path, "catalog-info.yaml", _component("api", "src/**") + "# pad\n" * 40)
    catalog = read_catalog(tmp_path)
    assert catalog.components == []
    (issue,) = catalog.issues
    assert issue.severity == "error" and issue.message == "larger than 100 bytes; not read"
    assert issue.source == "catalog-info.yaml"

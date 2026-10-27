"""Unit tests for ``repowise workspace`` command group."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli

WORKSPACE_CONFIG_FILENAME = ".repowise-workspace.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


def _write_workspace_config(root: Path, repos: list[dict], default_repo: str | None = None) -> None:
    """Write a minimal .repowise-workspace.yaml to *root*."""
    data: dict = {
        "version": 1,
        "default_repo": default_repo or (repos[0]["alias"] if repos else None),
        "repos": repos,
    }
    (root / WORKSPACE_CONFIG_FILENAME).write_text(
        yaml.dump(data, default_flow_style=False),
        encoding="utf-8",
    )


def _read_workspace_config(root: Path) -> dict:
    text = (root / WORKSPACE_CONFIG_FILENAME).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _make_git_repo(path: Path) -> None:
    """Create a minimal fake git repo at *path*."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


# ---------------------------------------------------------------------------
# workspace list
# ---------------------------------------------------------------------------


class TestWorkspaceList:
    def test_list_shows_repos(self, runner, tmp_path):
        repo_a = tmp_path / "service-a"
        repo_b = tmp_path / "service-b"
        repo_a.mkdir()
        repo_b.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "service-a", "alias": "service-a"},
                {"path": "service-b", "alias": "service-b"},
            ],
            default_repo="service-a",
        )

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0
        assert "service-a" in result.output
        assert "service-b" in result.output

    def test_list_shows_summary_line(self, runner, tmp_path):
        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[{"path": "repo-a", "alias": "repo-a"}],
        )

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0
        # Summary line: "X/Y repos indexed"
        assert "repos indexed" in result.output

    def test_list_counts_from_configured_db_url(self, runner, tmp_path, monkeypatch):
        # A configured DB URL with no local wiki.db still has counts to show.
        from repowise.cli.commands import status_cmd

        repo = tmp_path / "svc"
        (repo / ".repowise").mkdir(parents=True)
        _write_workspace_config(tmp_path, repos=[{"path": "svc", "alias": "svc"}])
        monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}")

        def _fake_run_async(coro):
            coro.close()
            return 7, 4321

        monkeypatch.setattr(status_cmd, "run_async", _fake_run_async)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "4,321" in result.output

    def test_list_marks_primary_repo(self, runner, tmp_path):
        repo_a = tmp_path / "main-svc"
        repo_a.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[{"path": "main-svc", "alias": "main-svc", "is_primary": True}],
            default_repo="main-svc",
        )

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0
        assert "primary" in result.output

    def test_list_no_workspace_errors(self, runner, tmp_path):
        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        # Should exit non-zero or print a helpful error — no workspace found
        assert result.exit_code != 0 or "No .repowise-workspace.yaml" in result.output


# ---------------------------------------------------------------------------
# workspace add
# ---------------------------------------------------------------------------


class TestWorkspaceAdd:
    def test_add_new_repo(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        new_repo = tmp_path / "new-service"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "add", str(new_repo)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "new-service" in result.output

        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "new-service" in aliases

    def test_add_with_custom_alias(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        new_repo = tmp_path / "my-long-service-name"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "add", str(new_repo), "--alias", "svc"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "svc" in aliases

    def test_add_nonexistent_path_errors(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        bad_path = str(tmp_path / "does-not-exist")

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "add", bad_path])

        assert result.exit_code != 0

    def test_add_non_git_path_errors(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "add", str(plain_dir)])

        assert result.exit_code != 0
        assert ".git" in result.output

    def test_add_duplicate_alias_errors(self, runner, tmp_path):
        existing_repo = tmp_path / "svc"
        existing_repo.mkdir()
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc", "alias": "svc"}],
        )
        new_repo = tmp_path / "other-svc"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "add", str(new_repo), "--alias", "svc"],
            )

        assert result.exit_code != 0
        assert "svc" in result.output

    def test_add_no_workspace_errors(self, runner, tmp_path):
        # Create a subdirectory that has no workspace config anywhere in its tree
        isolated = tmp_path / "no-ws"
        isolated.mkdir()
        new_repo = isolated / "svc"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(isolated)):
            result = runner.invoke(cli, ["workspace", "add", str(new_repo)])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# workspace remove
# ---------------------------------------------------------------------------


class TestWorkspaceRemove:
    def test_remove_existing_repo(self, runner, tmp_path):
        repo_a = tmp_path / "svc-a"
        repo_a.mkdir()
        repo_b = tmp_path / "svc-b"
        repo_b.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "svc-a", "alias": "svc-a"},
                {"path": "svc-b", "alias": "svc-b"},
            ],
            default_repo="svc-a",
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "remove", "svc-b"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "svc-b" not in aliases
        assert "svc-a" in aliases

    def test_remove_default_repo_shows_warning(self, runner, tmp_path):
        repo_a = tmp_path / "svc-a"
        repo_a.mkdir()
        repo_b = tmp_path / "svc-b"
        repo_b.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "svc-a", "alias": "svc-a"},
                {"path": "svc-b", "alias": "svc-b"},
            ],
            default_repo="svc-a",
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "remove", "svc-a"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        # Should mention the reassignment
        assert "default" in result.output.lower() or "Note" in result.output

    def test_remove_preserves_repowise_dir(self, runner, tmp_path):
        repo_a = tmp_path / "svc-a"
        repo_a.mkdir()
        repowise_dir = repo_a / ".repowise"
        repowise_dir.mkdir()
        sentinel = repowise_dir / "wiki.db"
        sentinel.write_text("data")

        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc-a", "alias": "svc-a"}],
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            runner.invoke(cli, ["workspace", "remove", "svc-a"])

        # Indexed data must still exist
        assert sentinel.exists()

    def test_remove_invalid_alias_errors(self, runner, tmp_path):
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc-a", "alias": "svc-a"}],
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "remove", "no-such-repo"])

        assert result.exit_code != 0
        assert "no-such-repo" in result.output

    def test_remove_no_workspace_errors(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "remove", "svc"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# workspace scan
# ---------------------------------------------------------------------------


class TestWorkspaceScan:
    def test_scan_discovers_new_repos(self, runner, tmp_path):
        # Workspace exists but has no repos registered
        _write_workspace_config(tmp_path, repos=[], default_repo=None)

        # Create a git repo inside the workspace root
        new_repo = tmp_path / "discovered-svc"
        _make_git_repo(new_repo)

        result = runner.invoke(
            cli,
            ["workspace", "scan", str(tmp_path), "--yes"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "discovered-svc" in result.output

        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "discovered-svc" in aliases

    def test_scan_skips_already_registered(self, runner, tmp_path):
        existing = tmp_path / "existing-svc"
        _make_git_repo(existing)
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "existing-svc", "alias": "existing-svc"}],
        )

        result = runner.invoke(
            cli,
            ["workspace", "scan", str(tmp_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "No new repositories" in result.output

    def test_scan_prompts_when_no_yes_flag(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        new_repo = tmp_path / "new-svc"
        _make_git_repo(new_repo)

        # Answer "n" to the prompt
        result = runner.invoke(
            cli,
            ["workspace", "scan", str(tmp_path)],
            input="n\n",
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "new-svc" not in aliases

    def test_scan_no_workspace_errors(self, runner, tmp_path):
        result = runner.invoke(cli, ["workspace", "scan", str(tmp_path)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# workspace set-default
# ---------------------------------------------------------------------------


class TestWorkspaceSetDefault:
    def test_set_default_changes_primary(self, runner, tmp_path):
        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "svc-a", "alias": "svc-a", "is_primary": True},
                {"path": "svc-b", "alias": "svc-b"},
            ],
            default_repo="svc-a",
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "set-default", "svc-b"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "svc-b" in result.output

        cfg = _read_workspace_config(tmp_path)
        assert cfg["default_repo"] == "svc-b"

        # is_primary flag should be set only on svc-b
        by_alias = {r["alias"]: r for r in cfg["repos"]}
        assert by_alias["svc-b"].get("is_primary") is True
        assert not by_alias["svc-a"].get("is_primary", False)

    def test_set_default_invalid_alias_errors(self, runner, tmp_path):
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc-a", "alias": "svc-a"}],
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "set-default", "nonexistent"])

        assert result.exit_code != 0
        assert "nonexistent" in result.output

    def test_set_default_no_workspace_errors(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "set-default", "svc-a"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Help smoke tests
# ---------------------------------------------------------------------------


class TestWorkspaceHelp:
    def test_workspace_help(self, runner):
        result = runner.invoke(cli, ["workspace", "--help"])
        assert result.exit_code == 0
        assert "workspace" in result.output

    def test_workspace_list_help(self, runner):
        result = runner.invoke(cli, ["workspace", "list", "--help"])
        assert result.exit_code == 0

    def test_workspace_add_help(self, runner):
        result = runner.invoke(cli, ["workspace", "add", "--help"])
        assert result.exit_code == 0
        assert "--alias" in result.output
        assert "--index" in result.output

    def test_workspace_remove_help(self, runner):
        result = runner.invoke(cli, ["workspace", "remove", "--help"])
        assert result.exit_code == 0

    def test_workspace_scan_help(self, runner):
        result = runner.invoke(cli, ["workspace", "scan", "--help"])
        assert result.exit_code == 0
        assert "--yes" in result.output

    def test_workspace_set_default_help(self, runner):
        result = runner.invoke(cli, ["workspace", "set-default", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# workspace diagnostics
# ---------------------------------------------------------------------------


class TestWorkspaceDiagnostics:
    def _setup(self, root: Path) -> None:
        from repowise.core.workspace.contracts import Contract, ContractLink
        from repowise.core.workspace.cross_repo import CrossRepoOverlay
        from repowise.core.workspace.system_graph import build_system_graph, save_system_graph

        (root / "backend").mkdir()
        (root / "frontend").mkdir()
        _write_workspace_config(
            root,
            repos=[
                {"path": "backend", "alias": "backend"},
                {"path": "frontend", "alias": "frontend"},
            ],
            default_repo="backend",
        )
        contracts = [
            Contract(repo="backend", contract_id="http::GET::/users", contract_type="http",
                     role="provider", file_path="r.py", symbol_name="h", confidence=0.9),
            Contract(repo="frontend", contract_id="http::GET::/users", contract_type="http",
                     role="consumer", file_path="c.ts", symbol_name="f", confidence=0.8),
            Contract(repo="backend", contract_id="http::GET::/orphan", contract_type="http",
                     role="provider", file_path="r.py", symbol_name="o", confidence=0.9),
        ]
        links = [
            ContractLink(contract_id="http::GET::/users", contract_type="http", match_type="exact",
                         confidence=0.72, provider_repo="backend", provider_file="r.py",
                         provider_symbol="h", provider_service=None, consumer_repo="frontend",
                         consumer_file="c.ts", consumer_symbol="f", consumer_service=None),
        ]
        graph = build_system_graph(contracts, links, CrossRepoOverlay(), {}, generated_at="t")
        save_system_graph(graph, root)

    def test_diagnostics_table(self, runner, tmp_path):
        self._setup(tmp_path)
        result = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "1 cross-repo link(s) matched" in result.output
        assert "orphan provider" in result.output
        assert "http::GET::/orphan" in result.output

    def test_diagnostics_json(self, runner, tmp_path):
        import json

        self._setup(tmp_path)
        result = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["total_links"] == 1
        assert data["total_providers"] == 2
        assert len(data["orphan_providers"]) == 1

    def test_diagnostics_missing_graph(self, runner, tmp_path):
        (tmp_path / "backend").mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "backend", "alias": "backend"}])
        result = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path)])
        assert result.exit_code != 0
        assert "No system graph found" in result.output

    def test_diagnostics_help(self, runner):
        result = runner.invoke(cli, ["workspace", "diagnostics", "--help"])
        assert result.exit_code == 0
        # ``--json`` still works but is hidden; ``--format`` is the one
        # documented spelling. See tests/unit/cli/test_format_json_rollout.py.
        assert "--format" in result.output
        assert "--json" not in result.output


class TestWorkspaceDiagnosticsReport:
    """Every section of the text report, over hand-built diagnostics."""

    def _save(self, root: Path, *, clean: bool = False, orphans: int = 23) -> None:
        from repowise.core.workspace.diagnostics import (
            ExtractionDiagnostics,
            OrphanProvider,
            RepoDiagnostics,
            UnmatchedConsumer,
        )
        from repowise.core.workspace.system_graph import SystemGraph, save_system_graph

        _write_workspace_config(
            root,
            repos=[{"path": "api", "alias": "api"}, {"path": "web", "alias": "web"}],
        )
        diag = ExtractionDiagnostics(
            total_providers=30,
            total_consumers=12,
            total_links=4,
            weak_link_count=0 if clean else 2,
            repo_breakdown=[
                RepoDiagnostics(
                    repo="api", providers_by_type={"http": 30}, provider_count=30
                ),
                RepoDiagnostics(
                    repo="web",
                    consumers_by_type={"http": 9, "topic": 3},
                    consumer_count=12,
                ),
                RepoDiagnostics(repo="gone"),
            ],
            providers_by_layer={} if clean else {"index": 20, "regex": 10},
            consumers_by_layer={} if clean else {"regex": 12},
            http_consumers_unresolved=3,
            unrecognised_call_files={"web": 0},
            http_consumers_linked=4,
        )
        if not clean:
            diag.unmatched_consumers = [
                UnmatchedConsumer("web", "a.ts", "http::GET::/x", "http", "no_provider"),
                UnmatchedConsumer("web", "b.ts", "http::GET::/y", "http", "mystery"),
            ]
            diag.unmatched_by_reason = {"no_provider": 1, "mystery": 1}
            diag.in_service_consumers = [
                UnmatchedConsumer("api", "s.py", "data::t", "data", "internal_only"),
            ]
            diag.orphan_providers = [
                OrphanProvider("api", f"r{i}.py", f"http::GET::/o{i}", "http")
                for i in range(orphans)
            ]
        save_system_graph(SystemGraph(diagnostics=diag, generated_at="not-a-date"), root)

    def test_every_section_renders(self, runner, tmp_path):
        self._save(tmp_path)
        result = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "stamped not-a-date" in out
        assert "Covers gone, which is no longer in" in out
        assert "http:30/0" in out
        assert "4 cross-repo link(s) matched from 30 provider(s) and 12 consumer(s)" in out
        assert "2 weak (low-confidence) link(s)" in out
        assert "Extraction coverage" in out
        assert "Providers   30 (20 index, 10 regex)" in out
        assert "Consumers   12 (12 regex)" in out
        assert "HTTP calls  4 of 12 linked to another service (33%); 9 read (75%)" in out
        assert "read, not linked: 1 match no declared route, 1 mystery" in out
        assert "2 unmatched consumer(s)" in out
        assert "dangling: no repo declares" in out
        assert "web http::GET::/x (a.ts)" in out
        assert "mystery" in out
        assert "1 consumer(s) served inside their own service, not cross-repo (1 data)" in out
        assert "23 orphan provider(s)" in out
        assert "http::GET::/o19" in out
        assert "http::GET::/o20" not in out
        assert "... and 3 more" in out
        assert "Every consumer matched" not in out

    def test_clean_report(self, runner, tmp_path):
        self._save(tmp_path, clean=True)
        result = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Extraction coverage" not in result.output
        assert "weak" not in result.output
        assert "Every consumer matched a provider; no orphan providers." in result.output

    def test_repo_filter_text(self, runner, tmp_path):
        self._save(tmp_path, orphans=2)
        result = runner.invoke(
            cli, ["workspace", "diagnostics", str(tmp_path), "--repo", "api"]
        )
        assert result.exit_code == 0, result.output
        assert "unmatched consumer" not in result.output
        assert "2 orphan provider(s)" in result.output
        assert "... and" not in result.output

    def test_repo_filter_json(self, runner, tmp_path):
        import json

        self._save(tmp_path)
        result = runner.invoke(
            cli,
            ["workspace", "diagnostics", str(tmp_path), "--repo", "web", "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert [r["repo"] for r in data["repo_breakdown"]] == ["web"]
        assert len(data["unmatched_consumers"]) == 2
        assert data["orphan_providers"] == []
        assert data["in_service_consumers"] == []
        assert data["http_consumers_linked"] == 4
        assert data["http_link_coverage"] == pytest.approx(4 / 12)
        # Totals and roll-ups are workspace-wide, not filtered.
        assert data["total_providers"] == 30
        assert data["unmatched_by_reason"] == {"no_provider": 1, "mystery": 1}
        assert data["http_consumer_coverage"] == 0.75
        assert data["generated_at"] == "not-a-date"

    def test_repo_filter_text_reason_counts(self, runner, tmp_path):
        from repowise.core.workspace.diagnostics import UnmatchedConsumer
        from repowise.core.workspace.system_graph import load_system_graph, save_system_graph

        self._save(tmp_path)
        graph = load_system_graph(tmp_path)
        diag = graph.diagnostics
        diag.unmatched_consumers.append(
            UnmatchedConsumer("api", "c.py", "http::GET::/z", "http", "internal_only")
        )
        diag.unmatched_by_reason["internal_only"] = 1
        save_system_graph(graph, tmp_path)

        result = runner.invoke(
            cli, ["workspace", "diagnostics", str(tmp_path), "--repo", "web"]
        )
        assert result.exit_code == 0, result.output
        assert "2 unmatched consumer(s)" in result.output
        # An old artifact's in-service row is read as in-service, and the
        # repo filter drops it with the rest of api's rows.
        assert "served inside their own service, not cross-repo" not in result.output

    def test_read_not_linked_split_adds_up(self, runner, tmp_path):
        """Read minus linked equals the split, built from real diagnostics."""
        from repowise.core.workspace.contracts import Contract, ContractLink
        from repowise.core.workspace.diagnostics import build_diagnostics
        from repowise.core.workspace.system_graph import SystemGraph, save_system_graph

        _write_workspace_config(
            tmp_path,
            repos=[{"path": "api", "alias": "api"}, {"path": "web", "alias": "web"}],
        )

        def c(repo, cid, role, file):
            return Contract(repo=repo, contract_id=cid, contract_type="http", role=role,
                            file_path=file, symbol_name="f", confidence=0.8)

        contracts = [
            c("api", "http::GET::/u", "provider", "h.py"),
            c("api", "http::GET::/own", "provider", "h.py"),
            c("api", "http::GET::/own", "consumer", "self.py"),
            c("web", "http::GET::/u", "consumer", "a.ts"),
            c("web", "http::GET::/none", "consumer", "b.ts"),
        ]
        links = [
            ContractLink(contract_id="http::GET::/u", contract_type="http", match_type="exact",
                         confidence=0.9, provider_repo="api", provider_file="h.py",
                         provider_symbol="f", provider_service=None, consumer_repo="web",
                         consumer_file="a.ts", consumer_symbol="f", consumer_service=None),
        ]
        diag = build_diagnostics(contracts, links, {"web": {"call_files_unrecognised": 1}})
        save_system_graph(SystemGraph(diagnostics=diag, generated_at="t"), tmp_path)

        out = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path)]).output
        assert "HTTP calls  1 of 4 linked to another service (25%); 3 read (75%)" in out
        assert "read, not linked: 1 match no declared route, 1 served inside their own service" in out
        assert "web http::GET::/none (b.ts)" in out
        assert "Topic calls" not in out  # an artifact before the messaging probe

    def test_topic_calls_line_names_the_unread_messaging_files(self, runner, tmp_path):
        from repowise.core.workspace.contracts import Contract
        from repowise.core.workspace.diagnostics import build_diagnostics
        from repowise.core.workspace.system_graph import SystemGraph, save_system_graph

        _write_workspace_config(tmp_path, repos=[{"path": "orders", "alias": "orders"}])
        contracts = [
            Contract(repo="orders", contract_id="topic::orders", contract_type="topic",
                     role="provider", file_path="p.py", symbol_name="f", confidence=0.8),
        ]
        stats = {"orders": {"topic_names_unresolved": 2, "message_files_unrecognised": 3}}
        diag = build_diagnostics(contracts, [], stats)
        save_system_graph(SystemGraph(diagnostics=diag, generated_at="t"), tmp_path)

        out = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path)]).output
        assert "Topic calls 1 read, 2 located but the name not statically resolvable, " in out
        assert "unread messaging calls: orders 3" in out

    def test_tables_collapse_to_one_line_per_repo(self, runner, tmp_path):
        from repowise.core.workspace.diagnostics import OrphanProvider, UnmatchedConsumer
        from repowise.core.workspace.system_graph import load_system_graph, save_system_graph

        self._save(tmp_path, orphans=1)
        graph = load_system_graph(tmp_path)
        diag = graph.diagnostics
        diag.unmatched_consumers = [
            UnmatchedConsumer("web", f"q{i}.ts", f"data::t{i % 2}", "data", "no_provider")
            for i in range(4)
        ] + [UnmatchedConsumer("web", "g.ts", "grpc::S/M", "grpc", "no_provider")]
        diag.orphan_providers += [
            OrphanProvider("api", "m.py", "data::users", "data", ["mig.py"]),
            OrphanProvider("api", "n.py", "data::zones", "data"),
        ]
        save_system_graph(graph, tmp_path)

        out = runner.invoke(cli, ["workspace", "diagnostics", str(tmp_path)]).output
        assert "5 unmatched consumer(s)" in out
        assert "web grpc::S/M (g.ts)" in out
        assert "web reads 2 table(s): t0, t1" in out
        assert "3 orphan provider(s)" in out
        assert "api http::GET::/o0 (r0.py)" in out
        assert "api declares 2 table(s) nothing reads: users, zones" in out


# ---------------------------------------------------------------------------
# workspace check
# ---------------------------------------------------------------------------


class TestWorkspaceCheck:
    def _write_config_with_rule(self, root: Path, rules: list[dict]) -> None:
        data = {
            "version": 1,
            "default_repo": "frontend",
            "repos": [
                {"path": "frontend", "alias": "frontend"},
                {"path": "db", "alias": "db"},
            ],
            "conformance": {"rules": rules},
        }
        (root / WORKSPACE_CONFIG_FILENAME).write_text(
            yaml.dump(data, default_flow_style=False), encoding="utf-8"
        )

    def _save_graph(self, root: Path, edges: list[tuple[str, str]]) -> None:
        from repowise.core.workspace.system_graph import (
            SystemEdge,
            SystemGraph,
            SystemNode,
            save_system_graph,
        )

        node_ids = {n for e in edges for n in e}
        graph = SystemGraph(
            nodes=[SystemNode(id=n, repo=n, service_path=None, name=n) for n in sorted(node_ids)],
            edges=[
                SystemEdge(
                    id=f"{s}->{t}:http",
                    source=s,
                    target=t,
                    kind="http",
                    match_type="exact",
                    confidence=1.0,
                    weight=1,
                    structural=True,
                )
                for s, t in edges
            ],
        )
        save_system_graph(graph, root)

    def test_check_reports_violation_and_cycle(self, runner, tmp_path):
        self._write_config_with_rule(tmp_path, [{"source": "frontend", "target": "db"}])
        # frontend -> db (violation) and db -> frontend (forms a cycle)
        self._save_graph(tmp_path, [("frontend", "db"), ("db", "frontend")])
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 1, result.output
        assert "violation" in result.output.lower()
        assert "cycle" in result.output.lower()
        assert "frontend !-> db" in result.output

    def test_a_tangle_past_the_budget_reads_at_least(self, runner, tmp_path, monkeypatch):
        import repowise.core.workspace.cycles as cy

        self._write_config_with_rule(tmp_path, [])
        names = [f"s{i}" for i in range(5)]
        self._save_graph(tmp_path, [(s, t) for s in names for t in names if s != t])
        monkeypatch.setattr(cy, "MAX_ENUMERATED_CYCLES", 60)
        check = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        metrics = runner.invoke(cli, ["workspace", "metrics", str(tmp_path)])
        assert check.exit_code == 1, check.output
        assert "at least 60 dependency cycle(s) (the 50 shortest listed)" in check.output
        assert "at least 60 cycle(s)" in check.output
        assert "at least 60" in metrics.output, metrics.output

    def test_a_cycle_closed_by_an_event_fails_and_names_each_leg(self, runner, tmp_path):
        from repowise.core.workspace.system_graph import (
            SystemEdge,
            SystemGraph,
            SystemNode,
            save_system_graph,
        )

        self._write_config_with_rule(tmp_path, [])

        def edge(s, t, kind):
            return SystemEdge(
                id=f"{s}->{t}:{kind}",
                source=s,
                target=t,
                kind=kind,
                match_type="exact",
                confidence=1.0,
                weight=1,
                structural=True,
            )

        save_system_graph(
            SystemGraph(
                nodes=[SystemNode(id=n, repo=n, service_path=None, name=n) for n in ("db", "frontend")],
                edges=[edge("frontend", "db", "http"), edge("db", "frontend", "event")],
            ),
            tmp_path,
        )
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 1, result.output
        # networkx picks the loop's starting node, so either rotation is right.
        assert (
            "db -[event]-> frontend -[http]-> db" in result.output
            or "frontend -[http]-> db -[event]-> frontend" in result.output
        )
        assert "1 of 2 leg(s) only an event joins" in result.output
        assert "a subscriber depends on its publisher" in result.output

    def test_check_clean_passes(self, runner, tmp_path):
        self._write_config_with_rule(tmp_path, [{"source": "frontend", "target": "db"}])
        # frontend never depends on db directly, and the graph is acyclic
        (tmp_path / "api").mkdir(exist_ok=True)
        self._save_graph(tmp_path, [("frontend", "api"), ("api", "db")])
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No violations" in result.output

    def test_check_json_exit_code(self, runner, tmp_path):
        import json

        self._write_config_with_rule(tmp_path, [{"source": "frontend", "target": "db"}])
        self._save_graph(tmp_path, [("frontend", "db")])
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path), "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["violation_count"] == 1

    def test_check_missing_graph(self, runner, tmp_path):
        self._write_config_with_rule(tmp_path, [])
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code != 0
        assert "No system graph found" in result.output

    def test_check_help(self, runner):
        result = runner.invoke(cli, ["workspace", "check", "--help"])
        assert result.exit_code == 0
        assert "non-zero" in result.output or "CI" in result.output


# ---------------------------------------------------------------------------
# workspace check — breaking contract changes
# ---------------------------------------------------------------------------


class TestWorkspaceCheckBreaking:
    """`check` fails on breaking contract changes without any rule declared."""

    def _write_config(self, root: Path) -> None:
        data = {
            "version": 1,
            "default_repo": "frontend",
            "repos": [
                {"path": "frontend", "alias": "frontend"},
                {"path": "api", "alias": "api"},
            ],
        }
        (root / WORKSPACE_CONFIG_FILENAME).write_text(
            yaml.dump(data, default_flow_style=False), encoding="utf-8"
        )

    def _save_graph(self, root: Path) -> None:
        from repowise.core.workspace.system_graph import (
            SystemGraph,
            SystemNode,
            save_system_graph,
        )

        save_system_graph(
            SystemGraph(
                nodes=[
                    SystemNode(id=n, repo=n, service_path=None, name=n)
                    for n in ("api", "frontend")
                ]
            ),
            root,
        )

    def _save_report(
        self,
        root: Path,
        *,
        consumer_repo: str,
        severity: str = "breaking",
        extra_consumers: tuple[str, ...] = (),
    ) -> None:
        from repowise.core.workspace.breaking_change import (
            BreakingChange,
            BreakingChangeReport,
            ImpactedConsumer,
            save_breaking_change_report,
        )

        save_breaking_change_report(
            BreakingChangeReport(
                generated_at="2026-08-23T00:00:00+00:00",
                changes=[
                    BreakingChange(
                        kind="removed_endpoint",
                        severity=severity,
                        contract_id="code::@acme/types::Order",
                        contract_type="code",
                        provider_repo="api",
                        provider_file="src/types.ts",
                        provider_symbol="Order",
                        provider_service=None,
                        detail="code::@acme/types::Order was removed",
                        impacted_consumers=[
                            ImpactedConsumer(
                                repo=repo,
                                service=None,
                                node_id=repo,
                                file=f"src/{i}.ts",
                                symbol="@acme/types:Order",
                                match_type="exact",
                                confidence=0.9,
                            )
                            for i, repo in enumerate((consumer_repo, *extra_consumers))
                        ],
                    )
                ],
            ),
            root,
        )

    def test_breaking_change_fails_check_by_default(self, runner, tmp_path):
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 1, result.output
        assert "code::@acme/types::Order" in result.output
        assert "1 breaking contract change(s)" in result.output

    def test_no_breaking_flag_restores_the_old_gate(self, runner, tmp_path):
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path), "--no-breaking"])
        assert result.exit_code == 0, result.output
        assert "@acme/types::Order" not in result.output

    def test_internal_only_change_does_not_fail(self, runner, tmp_path):
        """A provider whose only consumer is its own repo is not a cross-repo break."""
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="api")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No breaking contract changes" in result.output

    def test_json_carries_the_breaking_changes(self, runner, tmp_path):
        import json

        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path), "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["violation_count"] == 0
        assert [c["contract_id"] for c in data["breaking_changes"]] == [
            "code::@acme/types::Order"
        ]

    def test_no_report_leaves_check_silent(self, runner, tmp_path):
        """Nothing said when the workspace has never run a detection pass."""
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "breaking contract" not in result.output

    def test_a_warning_severity_does_not_fail_the_build(self, runner, tmp_path):
        """A removed request field is source-compat only, not wire-incompatible."""
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend", severity="warning")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No breaking contract changes" in result.output

    def test_the_consumer_count_is_the_cross_repo_one(self, runner, tmp_path):
        """Two of the three consumers are in the provider's own repo."""
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(
            tmp_path, consumer_repo="frontend", extra_consumers=("api", "api")
        )
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 1, result.output
        assert "1 consumer(s) in frontend" in result.output

    def test_the_report_stamp_is_shown_beside_the_finding(self, runner, tmp_path):
        """The graph is recomputed now; this artifact can be any age."""
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert "2026-08-23T00:00:00+00:00" in result.output

    def test_json_stamps_the_breaking_half_separately(self, runner, tmp_path):
        import json

        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path), "--json"])
        data = json.loads(result.output)
        assert data["breaking_changes_generated_at"] == "2026-08-23T00:00:00+00:00"
        assert data["breaking_changes_available"] is True

    def test_json_says_when_nothing_ever_looked(self, runner, tmp_path):
        """An empty list without this flag reads as a pass in CI."""
        import json

        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["breaking_changes"] == []
        assert data["breaking_changes_available"] is False

    def test_the_failure_line_omits_a_count_nothing_produced(self, runner, tmp_path):
        """With no report there is no 0 to claim."""
        self._write_config_with_rule_and_violation(tmp_path)
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 1
        assert "breaking contract change(s)." not in result.output
        assert "cycle(s)." in result.output

    def _write_config_with_rule_and_violation(self, root: Path) -> None:
        from repowise.core.workspace.system_graph import (
            SystemEdge,
            SystemGraph,
            SystemNode,
            save_system_graph,
        )

        data = {
            "version": 1,
            "default_repo": "frontend",
            "repos": [
                {"path": "frontend", "alias": "frontend"},
                {"path": "api", "alias": "api"},
            ],
            "conformance": {"rules": [{"source": "frontend", "target": "api"}]},
        }
        (root / WORKSPACE_CONFIG_FILENAME).write_text(
            yaml.dump(data, default_flow_style=False), encoding="utf-8"
        )
        save_system_graph(
            SystemGraph(
                nodes=[
                    SystemNode(id=n, repo=n, service_path=None, name=n)
                    for n in ("api", "frontend")
                ],
                edges=[
                    SystemEdge(
                        id="frontend->api:http",
                        source="frontend",
                        target="api",
                        kind="http",
                        match_type="exact",
                        confidence=1.0,
                        weight=1,
                        structural=True,
                    )
                ],
            ),
            root,
        )

    def test_format_json_matches_the_legacy_flag(self, runner, tmp_path):
        import json

        self._write_config_with_rule_and_violation(tmp_path)
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path), "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["violation_count"] == 1
        assert data["breaking_changes"] == []
        assert data["breaking_changes_available"] is False

    def test_no_rules_and_no_cycles_text(self, runner, tmp_path):
        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Checked 2 service(s) across 2 repo(s) against 0 declared rule(s)." in result.output
        assert "No conformance rules declared." in result.output
        # Two services and no edge between them: "no cycles" would be vacuous.
        assert "Not determined:" in result.output
        assert "no dependency between services was detected" in result.output
        assert "Dependency cycles not determined" in result.output
        assert "No dependency cycles." not in result.output
        assert "No violations" not in result.output

    def test_not_determined_json_says_so(self, runner, tmp_path):
        import json

        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["cycles_determined"] is False
        assert data["evidence"]["state"] == "insufficient"

    def test_no_breaking_json_omits_the_breaking_half(self, runner, tmp_path):
        import json

        self._write_config(tmp_path)
        self._save_graph(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend")
        result = runner.invoke(
            cli, ["workspace", "check", str(tmp_path), "--no-breaking", "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "breaking_changes" not in data
        assert "breaking_changes_available" not in data

    def test_failure_line_counts_violations_and_breaking_changes(self, runner, tmp_path):
        self._write_config_with_rule_and_violation(tmp_path)
        self._save_report(tmp_path, consumer_repo="frontend")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 1, result.output
        assert "1 architecture rule violation(s)" in result.output
        assert "(detected 2026-08-23T00:00:00+00:00)" in result.output
        assert (
            "Architecture check failed: 1 violation(s), 0 cycle(s), "
            "1 breaking contract change(s)."
        ) in result.output

    def test_rule_description_is_printed_under_the_violation(self, runner, tmp_path):
        self._write_config_with_rule_and_violation(tmp_path)
        cfg_path = tmp_path / WORKSPACE_CONFIG_FILENAME
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        data["conformance"]["rules"][0]["description"] = "UI talks to the gateway only"
        cfg_path.write_text(yaml.dump(data), encoding="utf-8")
        result = runner.invoke(cli, ["workspace", "check", str(tmp_path)])
        assert result.exit_code == 1, result.output
        assert "UI talks to the gateway only" in result.output


# ---------------------------------------------------------------------------
# workspace metrics
# ---------------------------------------------------------------------------


class TestWorkspaceMetrics:
    def _write(self, root: Path, edges: list[tuple[str, str]], rules: list[dict]) -> None:
        from repowise.core.workspace.system_graph import (
            SystemEdge,
            SystemGraph,
            SystemNode,
            save_system_graph,
        )

        names = sorted({n for e in edges for n in e})
        data = {
            "version": 1,
            "default_repo": names[0] if names else None,
            "repos": [{"path": n, "alias": n} for n in names],
            "conformance": {"rules": rules},
        }
        (root / WORKSPACE_CONFIG_FILENAME).write_text(
            yaml.dump(data, default_flow_style=False), encoding="utf-8"
        )
        save_system_graph(
            SystemGraph(
                nodes=[SystemNode(id=n, repo=n, service_path=None, name=n) for n in names],
                edges=[
                    SystemEdge(
                        id=f"{s}->{t}:http",
                        source=s,
                        target=t,
                        kind="http",
                        match_type="exact",
                        confidence=1.0,
                        weight=1,
                        structural=True,
                    )
                    for s, t in edges
                ],
            ),
            root,
        )

    def test_metrics_table_reports_core_and_violations(self, runner, tmp_path):
        self._write(
            tmp_path,
            [("frontend", "db"), ("db", "frontend"), ("api", "db")],
            [{"source": "frontend", "target": "db"}],
        )
        result = runner.invoke(cli, ["workspace", "metrics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Architecture score" in result.output
        assert "Propagation cost" in result.output
        assert "Cyclic core" in result.output
        assert "Rule violations" in result.output
        assert "Service roles:" in result.output

    def test_metrics_table_acyclic(self, runner, tmp_path):
        self._write(tmp_path, [("frontend", "api"), ("api", "db")], [])
        result = runner.invoke(cli, ["workspace", "metrics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "acyclic structure" in result.output
        assert "Rule violations" not in result.output

    def test_metrics_format_json(self, runner, tmp_path):
        import json

        self._write(
            tmp_path,
            [("frontend", "db"), ("db", "frontend")],
            [{"source": "frontend", "target": "db"}],
        )
        result = runner.invoke(cli, ["workspace", "metrics", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["node_count"] == 2
        assert data["core_size"] == 2
        assert data["conformance_violations"] == 1
        assert set(data["role_breakdown"]) >= {"core", "shared", "control", "peripheral"}

    def test_metrics_with_no_edges_is_not_determined(self, runner, tmp_path):
        import json

        self._write(tmp_path, [], [])
        from repowise.core.workspace.system_graph import (
            SystemGraph,
            SystemNode,
            save_system_graph,
        )

        save_system_graph(
            SystemGraph(
                nodes=[SystemNode(id=n, repo=n, service_path=None, name=n) for n in ("a", "b")],
                repo_provenance={"a": {}, "b": {}, "c": {}},
            ),
            tmp_path,
        )
        result = runner.invoke(cli, ["workspace", "metrics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Architecture score  not determined" in result.output
        assert "10.0" not in result.output
        assert "1 repo(s) were read but yielded no contracts" in result.output
        assert "Service roles" not in result.output
        data = json.loads(
            runner.invoke(cli, ["workspace", "metrics", str(tmp_path), "--format", "json"]).output
        )
        assert data["score"] is None
        assert data["cycle_count"] is None
        assert data["evidence"]["state"] == "insufficient"

    def test_a_repo_read_without_contracts_is_not_told_to_rerun(self, runner, tmp_path):
        from repowise.core.workspace.system_graph import (
            SystemEdge,
            SystemGraph,
            SystemNode,
            save_system_graph,
        )

        _write_workspace_config(
            tmp_path, [{"path": n, "alias": n} for n in ("api", "web", "docs", "new")]
        )
        save_system_graph(
            SystemGraph(
                nodes=[SystemNode(id=n, repo=n, service_path=None, name=n) for n in ("api", "web")],
                edges=[
                    SystemEdge(
                        id="web->api:http",
                        source="web",
                        target="api",
                        kind="http",
                        match_type="exact",
                        confidence=1.0,
                        weight=1,
                        structural=True,
                    )
                ],
                repo_provenance={"api": {}, "web": {}, "docs": {}},
            ),
            tmp_path,
        )
        result = runner.invoke(cli, ["workspace", "metrics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        out = " ".join(result.output.split())
        assert "docs was read but yielded no contracts" in out
        assert "new is configured but absent from this artifact" in out
        assert "docs is configured but absent" not in out
        assert "Partial evidence" in out

    def test_metrics_empty_graph(self, runner, tmp_path):
        self._write(tmp_path, [], [])
        result = runner.invoke(cli, ["workspace", "metrics", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No services in the system graph yet" in result.output

    def test_metrics_missing_graph(self, runner, tmp_path):
        _write_workspace_config(tmp_path, [{"path": "a", "alias": "a"}])
        result = runner.invoke(cli, ["workspace", "metrics", str(tmp_path)])
        assert result.exit_code != 0
        assert "No system graph found" in result.output


# ---------------------------------------------------------------------------
# workspace impacted-tests
# ---------------------------------------------------------------------------


class TestWorkspaceImpactedTests:
    """The command's own reporting, over a canned result.

    The join itself is covered in tests/unit/workspace/test_test_impact.py;
    what matters here is that a link the join could not follow reaches the
    reader instead of vanishing.
    """

    def _canned(self):
        from repowise.core.workspace.test_impact import (
            UnresolvedLink,
            WorkspaceTestImpactResult,
            WorkspaceTestRecommendation,
        )

        rec = WorkspaceTestRecommendation(
            test_id="tests/api.test.ts::it_getUser",
            test_file="tests/api.test.ts",
            consumer_repo="frontend",
            consumer_files=["src/api.ts"],
            consumer_symbol_ids=["src/api.ts::getUser"],
            provider_repo="backend",
            contract_ids=["http::GET::/users"],
            contract_types=["http"],
            basis="inferred",
            via="call-graph",
            confidence=0.9,
            source_files=["app/routers/users.py"],
            evidence=[
                {
                    "basis": "inferred",
                    "via": "call-graph",
                    "entry": "symbol",
                    "contract_id": "http::GET::/users",
                    "source_file": "app/routers/users.py",
                }
            ],
        )
        unresolved = UnresolvedLink(
            consumer_repo="frontend",
            consumer_file="src/legacy.ts",
            consumer_symbol_id=None,
            provider_repo="backend",
            provider_file="app/routers/users.py",
            contract_id="http::POST::/users",
            contract_type="http",
            reason="unbound",
            detail=None,
        )
        return WorkspaceTestImpactResult(
            workspace=True,
            recommendations=[rec],
            recommendations_total=1,
            recommendations_emitted=1,
            recommendations_truncated=False,
            recommendations_omitted=0,
            recommendations_by_basis={"measured": 0, "inferred": 1},
            recommendations_by_repo={"backend": 1},
            recommendations_by_consumer_repo={"frontend": 1},
            unresolved=[unresolved],
            files_analyzed=[],
            summary={
                "states": {"measured": 0, "inferred": 1, "none": 0, "unresolved": 1},
                "passes": {"measured": True, "inferred": True},
            },
        )

    def _empty(self):
        """What the command gets when the workspace has no contract map yet."""
        from repowise.core.workspace.test_impact import WorkspaceTestImpactResult

        return WorkspaceTestImpactResult(
            summary={
                "reason": "no_contract_store",
                "passes": {"measured": True, "inferred": True},
            }
        )

    def _install(self, tmp_path, monkeypatch, canned):
        from repowise.core.workspace import test_impact

        _make_git_repo(tmp_path / "backend")
        _make_git_repo(tmp_path / "frontend")
        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "backend", "alias": "backend"},
                {"path": "frontend", "alias": "frontend"},
            ],
        )

        async def _fake(root, changed, **options):
            return canned

        monkeypatch.setattr(test_impact, "workspace_test_impact_from_root", _fake)
        return tmp_path

    @pytest.fixture
    def workspace(self, tmp_path, monkeypatch):
        return self._install(tmp_path, monkeypatch, self._canned())

    @pytest.fixture
    def empty_workspace(self, tmp_path, monkeypatch):
        return self._install(tmp_path, monkeypatch, self._empty())

    def test_a_link_that_could_not_be_followed_is_printed(self, runner, workspace):
        result = runner.invoke(
            cli,
            [
                "workspace",
                "impacted-tests",
                "backend:app/routers/users.py",
                "--path",
                str(workspace),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Could not determine" in result.output
        assert "contract never bound to a symbol" in result.output

    def test_list_format_prints_the_test_file(self, runner, workspace):
        result = runner.invoke(
            cli,
            [
                "workspace",
                "impacted-tests",
                "backend:app/routers/users.py",
                "--path",
                str(workspace),
                "--format",
                "list",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "frontend:tests/api.test.ts" in result.output

    def test_the_table_names_the_contracts_and_the_provider_files(
        self, runner, workspace
    ):
        result = runner.invoke(
            cli,
            [
                "workspace",
                "impacted-tests",
                "backend:app/routers/users.py",
                "--path",
                str(workspace),
            ],
        )
        assert result.exit_code == 0, result.output
        flattened = " ".join(result.output.split())
        assert "http::GET::/users" in flattened
        assert "Provider Files" in flattened
        assert "app/routers/users.py" in flattened

    def _split_runner(self):
        """A runner that keeps stderr out of ``stdout``.

        ``mix_stderr`` exists on click 8.1 and was removed in 8.2, where the
        two streams are already separate.
        """
        import inspect

        from click.testing import CliRunner

        if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
            return CliRunner(mix_stderr=False)
        return CliRunner()

    def test_list_format_explains_an_empty_answer_on_stderr(self, empty_workspace):
        """A pipeline reading stdout must never get silence with exit 0."""
        result = self._split_runner().invoke(
            cli,
            [
                "workspace",
                "impacted-tests",
                "backend:app/routers/users.py",
                "--path",
                str(empty_workspace),
                "--format",
                "list",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == ""
        assert "No tests found" in result.stderr
        assert "contract map" in result.stderr

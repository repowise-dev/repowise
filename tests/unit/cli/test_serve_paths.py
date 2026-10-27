"""Safety net for `repowise serve`.

Pins the embedder prompt, port fallback, web asset resolution (download,
local build, staleness), frontend launch and the command's branch order.
No real server, socket listener, subprocess or network call is made.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from repowise.cli.commands import serve_cmd
from repowise.cli.commands.serve_cmd import (
    _advisory,
    _embedder,
    _frontend,
    _ports,
    _web_assets,
)

from ._real_call_guard import guard_real_calls


@pytest.fixture(autouse=True)
def _no_real_calls(monkeypatch) -> None:
    guard_real_calls(monkeypatch)


def _own_env(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Clear *names* so monkeypatch restores them even after raw assignment."""
    for name in names:
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)


_EMBED_ENV = (
    "REPOWISE_EMBEDDER",
    "REPOWISE_EMBEDDING_MODEL",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
)


# ---------------------------------------------------------------------------
# _setup_embedder
# ---------------------------------------------------------------------------


@pytest.fixture
def embed_env(monkeypatch, tmp_path) -> dict:
    """Clean embedder env, a tmp global config dir and a scripted prompt."""
    _own_env(monkeypatch, *_EMBED_ENV)
    monkeypatch.setattr(_embedder, "_GLOBAL_CONFIG_DIR", tmp_path / "global")
    state: dict = {"config": {}, "answers": [], "prompts": []}
    monkeypatch.setattr(_embedder, "load_config", lambda _p: state["config"])

    def fake_prompt(text, default=None, **_kw):
        state["prompts"].append((text.strip(), default))
        return state["answers"].pop(0)

    monkeypatch.setattr(_embedder.click, "prompt", fake_prompt)
    return state


def _saved_global(tmp_path: Path) -> dict:
    import yaml

    return yaml.safe_load((tmp_path / "global" / "config.yaml").read_text(encoding="utf-8"))


def test_embedder_env_already_set_skips_everything(embed_env, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")
    embed_env["config"] = {"embedder": "gemini"}
    serve_cmd._setup_embedder()
    assert os.environ["REPOWISE_EMBEDDER"] == "openai"
    assert embed_env["prompts"] == []


def test_saved_embedder_restores_env_and_key(embed_env) -> None:
    embed_env["config"] = {"embedder": "openrouter", "embedder_api_key": "sk-or"}
    serve_cmd._setup_embedder()
    assert os.environ["REPOWISE_EMBEDDER"] == "openrouter"
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or"
    assert embed_env["prompts"] == []


def test_saved_key_never_overwrites_exported_key(embed_env, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    embed_env["config"] = {"embedder": "openai", "embedder_api_key": "from-config"}
    serve_cmd._setup_embedder()
    assert os.environ["OPENAI_API_KEY"] == "from-env"


def test_saved_mock_embedder_falls_through_to_prompt(embed_env, tmp_path) -> None:
    embed_env["config"] = {"embedder": "mock"}
    embed_env["answers"] = ["skip"]
    serve_cmd._setup_embedder()
    assert "REPOWISE_EMBEDDER" not in os.environ
    assert embed_env["prompts"] == [("Select", "3")]
    assert not (tmp_path / "global").exists()


@pytest.mark.parametrize(
    ("key_var", "default"),
    [(None, "3"), ("GEMINI_API_KEY", "1"), ("GOOGLE_API_KEY", "1"), ("OPENAI_API_KEY", "1")],
)
def test_prompt_default_follows_exported_keys(embed_env, monkeypatch, key_var, default) -> None:
    if key_var:
        monkeypatch.setenv(key_var, "k")
    embed_env["answers"] = ["4"]
    serve_cmd._setup_embedder()
    assert embed_env["prompts"][0] == ("Select", default)
    assert "REPOWISE_EMBEDDER" not in os.environ


@pytest.mark.parametrize("answer", ["9", "0", "nonsense", "skip", "4"])
def test_unknown_or_skip_answer_skips(embed_env, answer) -> None:
    embed_env["answers"] = [answer]
    serve_cmd._setup_embedder()
    assert "REPOWISE_EMBEDDER" not in os.environ
    assert len(embed_env["prompts"]) == 1


def test_numbered_choice_with_exported_key_saves_without_reprompt(
    embed_env, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    embed_env["answers"] = ["2"]
    serve_cmd._setup_embedder()
    assert os.environ["REPOWISE_EMBEDDER"] == "openai"
    assert len(embed_env["prompts"]) == 1
    # The exported key is persisted alongside the choice.
    assert _saved_global(tmp_path) == {"embedder": "openai", "embedder_api_key": "sk-env"}


def test_named_choice_prompts_for_missing_key(embed_env, tmp_path) -> None:
    embed_env["answers"] = ["gemini", "  sk-typed  "]
    serve_cmd._setup_embedder()
    assert os.environ["REPOWISE_EMBEDDER"] == "gemini"
    assert os.environ["GEMINI_API_KEY"] == "sk-typed"
    assert [p[0] for p in embed_env["prompts"]] == ["Select", "GEMINI_API_KEY"]
    assert _saved_global(tmp_path) == {"embedder": "gemini", "embedder_api_key": "sk-typed"}


def test_blank_key_saves_choice_only_and_merges_existing(embed_env, tmp_path) -> None:
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "config.yaml").write_text("other: 1\n", encoding="utf-8")
    embed_env["answers"] = ["3", ""]
    serve_cmd._setup_embedder()
    assert os.environ["REPOWISE_EMBEDDER"] == "openrouter"
    assert "OPENROUTER_API_KEY" not in os.environ
    assert _saved_global(tmp_path) == {"other": 1, "embedder": "openrouter"}


def test_get_or_prompt_api_key_unknown_embedder_is_blank() -> None:
    assert serve_cmd._get_or_prompt_api_key("ollama") == ""


def test_set_api_key_env_ignores_blank_and_unknown(monkeypatch) -> None:
    _own_env(monkeypatch, *_EMBED_ENV)
    serve_cmd._set_api_key_env("openai", "")
    serve_cmd._set_api_key_env("ollama", "k")
    assert not any(n in os.environ for n in _EMBED_ENV)


# ---------------------------------------------------------------------------
# _load_local_provider_config
# ---------------------------------------------------------------------------


def _write_local_repowise(root: Path, *, env: str = "", config: str = "") -> None:
    (root / ".repowise").mkdir()
    if env:
        (root / ".repowise" / ".env").write_text(env, encoding="utf-8")
    if config:
        (root / ".repowise" / "config.yaml").write_text(config, encoding="utf-8")


def test_local_provider_config_noop_without_repowise_dir(monkeypatch, tmp_path) -> None:
    _own_env(monkeypatch, *_EMBED_ENV)
    monkeypatch.chdir(tmp_path)
    serve_cmd._load_local_provider_config()
    assert "REPOWISE_EMBEDDER" not in os.environ


def test_local_provider_config_loads_env_and_seeds_embedder(monkeypatch, tmp_path) -> None:
    _own_env(monkeypatch, *_EMBED_ENV)
    _write_local_repowise(
        tmp_path,
        env="OPENAI_API_KEY=sk-local\n",
        config="embedder: mock\nembedding_model: tiny-8\n",
    )
    monkeypatch.chdir(tmp_path)
    serve_cmd._load_local_provider_config()
    assert os.environ["OPENAI_API_KEY"] == "sk-local"
    # "mock" is seeded too: it pins the on-disk vector width.
    assert os.environ["REPOWISE_EMBEDDER"] == "mock"
    assert os.environ["REPOWISE_EMBEDDING_MODEL"] == "tiny-8"


def test_local_provider_config_never_overrides_env(monkeypatch, tmp_path) -> None:
    _own_env(monkeypatch, *_EMBED_ENV)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("REPOWISE_EMBEDDER", "gemini")
    monkeypatch.setenv("REPOWISE_EMBEDDING_MODEL", "m-env")
    _write_local_repowise(
        tmp_path,
        env="OPENAI_API_KEY=sk-local\n",
        config="embedder: openai\nembedding_model: m-cfg\n",
    )
    monkeypatch.chdir(tmp_path)
    serve_cmd._load_local_provider_config()
    assert os.environ["OPENAI_API_KEY"] == "sk-env"
    assert os.environ["REPOWISE_EMBEDDER"] == "gemini"
    assert os.environ["REPOWISE_EMBEDDING_MODEL"] == "m-env"


# ---------------------------------------------------------------------------
# Port probing
# ---------------------------------------------------------------------------


def test_find_free_port_scans_upward(monkeypatch) -> None:
    probed: list[int] = []

    def fake_free(_host, port):
        probed.append(port)
        return port == 3002

    monkeypatch.setattr(_ports, "_is_port_free", fake_free)
    assert serve_cmd._find_free_port("127.0.0.1", 3000, "web UI") == 3002
    assert probed == [3000, 3001, 3002]


def test_find_free_port_respects_max_attempts(monkeypatch) -> None:
    probed: list[int] = []

    def fake_free(_host, port):
        probed.append(port)
        return False

    monkeypatch.setattr(_ports, "_is_port_free", fake_free)
    serve_cmd._find_free_port("127.0.0.1", 4000, "API server", max_attempts=3)
    assert probed == [4000, 4001, 4002, 4003]


def test_find_free_port_stops_scanning_at_65535(monkeypatch) -> None:
    probed: list[int] = []

    def fake_free(_host, port):
        probed.append(port)
        return False

    monkeypatch.setattr(_ports, "_is_port_free", fake_free)
    chosen = serve_cmd._find_free_port("127.0.0.1", 65534, "API server")
    assert probed == [65534, 65535]
    assert isinstance(chosen, int) and chosen > 0


# ---------------------------------------------------------------------------
# Web asset resolution
# ---------------------------------------------------------------------------


@pytest.fixture
def web_cache(monkeypatch, tmp_path) -> Path:
    cache = tmp_path / "web-cache"
    monkeypatch.setattr(_web_assets, "_WEB_CACHE_DIR", cache)
    monkeypatch.setattr(_web_assets, "_MARKER_FILE", cache / ".version")
    return cache


def test_web_is_cached_requires_server_js_and_matching_marker(web_cache) -> None:
    assert serve_cmd._web_is_cached("1.0.0") is False
    web_cache.mkdir()
    (web_cache / "server.js").write_text("")
    assert serve_cmd._web_is_cached("1.0.0") is False  # no marker
    (web_cache / ".version").write_text("0.9.0\n")
    assert serve_cmd._web_is_cached("1.0.0") is False
    (web_cache / ".version").write_text("1.0.0\n")
    assert serve_cmd._web_is_cached("1.0.0") is True


def test_find_local_web_walks_up_from_cwd(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "packages" / "web").mkdir(parents=True)
    (repo / "packages" / "web" / "package.json").write_text("{}")
    deep = repo / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.setattr(_web_assets, "__file__", str(tmp_path / "x" / "y" / "serve_cmd.py"))
    monkeypatch.chdir(deep)
    assert serve_cmd._find_local_web() == (repo / "packages" / "web").resolve()


def test_find_local_web_none_without_package_json(monkeypatch, tmp_path) -> None:
    (tmp_path / "packages" / "web").mkdir(parents=True)  # no package.json
    monkeypatch.setattr(_web_assets, "__file__", str(tmp_path / "x" / "serve_cmd.py"))
    monkeypatch.chdir(tmp_path)
    assert serve_cmd._find_local_web() is None


def _make_web_tree(root: Path) -> tuple[Path, Path]:
    """A monorepo with packages/web, ui and types sources plus a built bundle."""
    web = root / "packages" / "web"
    server_js = web / ".next" / "standalone" / "packages" / "web" / "server.js"
    server_js.parent.mkdir(parents=True)
    server_js.write_text("")
    (web / "package.json").write_text("{}")
    (web / "src").mkdir()
    (web / "src" / "page.tsx").write_text("")
    (root / "packages" / "ui" / "src").mkdir(parents=True)
    (root / "packages" / "ui" / "src" / "button.tsx").write_text("")
    (web / "src" / "node_modules").mkdir()
    (web / "src" / "node_modules" / "dep.js").write_text("")
    old, built = 1_000_000, 2_000_000
    for p in root.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
    os.utime(server_js, (built, built))
    return web, server_js


def test_local_build_stale_without_bundle(tmp_path) -> None:
    web = tmp_path / "packages" / "web"
    web.mkdir(parents=True)
    assert serve_cmd._local_build_is_stale(web) is True


def test_local_build_fresh_when_sources_older(tmp_path) -> None:
    web, _ = _make_web_tree(tmp_path)
    assert serve_cmd._local_build_is_stale(web) is False


@pytest.mark.parametrize(
    "newer",
    ["packages/web/package.json", "packages/web/src/page.tsx", "packages/ui/src/button.tsx"],
)
def test_local_build_stale_when_an_input_is_newer(tmp_path, newer) -> None:
    web, _ = _make_web_tree(tmp_path)
    os.utime(tmp_path / newer, (3_000_000, 3_000_000))
    assert serve_cmd._local_build_is_stale(web) is True


def test_local_build_ignores_skipped_dirs(tmp_path) -> None:
    web, _ = _make_web_tree(tmp_path)
    os.utime(web / "src" / "node_modules" / "dep.js", (3_000_000, 3_000_000))
    assert serve_cmd._local_build_is_stale(web) is False


@pytest.mark.parametrize("parent", ["dist", ".git"])
def test_local_build_stale_inside_skipped_parent_dir(tmp_path, parent) -> None:
    repo = tmp_path / parent / "repo"
    web, _ = _make_web_tree(repo)
    assert serve_cmd._local_build_is_stale(web) is False
    os.utime(repo / "packages" / "web" / "src" / "page.tsx", (3_000_000, 3_000_000))
    assert serve_cmd._local_build_is_stale(web) is True


def _tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def fake_download(monkeypatch, tmp_path) -> dict:
    """Route httpx.stream to canned bytes and tempfile into a watched dir."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_dir))
    state: dict = {"body": b"", "error": None, "urls": [], "tmp_dir": tmp_dir}

    class _Resp:
        def raise_for_status(self) -> None:
            if state["error"] is not None:
                raise state["error"]

        def iter_bytes(self, chunk_size: int):
            body = state["body"]
            for i in range(0, len(body), chunk_size):
                yield body[i : i + chunk_size]

    @contextmanager
    def fake_stream(method, url, **_kw):
        state["urls"].append((method, url))
        yield _Resp()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    return state


def test_download_web_replaces_cache_and_writes_marker(web_cache, fake_download) -> None:
    web_cache.mkdir()
    (web_cache / "stale.js").write_text("old")
    (web_cache / "old_dir").mkdir()
    fake_download["body"] = _tarball({"server.js": b"// new", ".next/x.js": b"1"})

    assert serve_cmd._download_web("1.2.3") is True
    assert fake_download["urls"] == [
        (
            "GET",
            "https://github.com/repowise-dev/repowise/releases/download/v1.2.3/repowise-web.tar.gz",
        )
    ]
    assert sorted(p.name for p in web_cache.iterdir()) == [".next", ".version", "server.js"]
    assert (web_cache / ".version").read_text() == "1.2.3"
    assert list(fake_download["tmp_dir"].iterdir()) == []


def test_download_web_http_error_keeps_cache(web_cache, fake_download) -> None:
    web_cache.mkdir()
    (web_cache / "server.js").write_text("old")
    request = httpx.Request("GET", "https://example.invalid")
    fake_download["error"] = httpx.HTTPStatusError(
        "404", request=request, response=httpx.Response(404, request=request)
    )
    assert serve_cmd._download_web("1.2.3") is False
    assert (web_cache / "server.js").read_text() == "old"
    assert list(fake_download["tmp_dir"].iterdir()) == []


def test_download_web_bad_archive_returns_false(web_cache, fake_download) -> None:
    fake_download["body"] = b"not a tarball"
    assert serve_cmd._download_web("1.2.3") is False
    assert not (web_cache / ".version").exists()
    assert list(fake_download["tmp_dir"].iterdir()) == []


@pytest.fixture
def fake_run(monkeypatch) -> dict:
    """Record subprocess.run calls; raise on the call numbered ``fail_at``."""
    st: dict = {"calls": [], "fail_at": None}

    def _run(cmd, **kw):
        st["calls"].append((cmd, kw))
        if len(st["calls"]) == st["fail_at"]:
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(_web_assets.subprocess, "run", _run)
    return st


def test_build_local_web_installs_then_builds(tmp_path, fake_run) -> None:
    calls = fake_run["calls"]
    assert serve_cmd._build_local_web(tmp_path, "npm") is True
    assert [c[0] for c in calls] == [["npm", "install"], ["npm", "run", "build"]]
    assert all(c[1]["cwd"] == str(tmp_path) and c[1]["check"] for c in calls)


def test_build_local_web_skips_install_with_node_modules(tmp_path, fake_run) -> None:
    calls = fake_run["calls"]
    (tmp_path / "node_modules").mkdir()
    assert serve_cmd._build_local_web(tmp_path, "npm") is True
    assert [c[0] for c in calls] == [["npm", "run", "build"]]


def test_build_local_web_failure_returns_false(tmp_path, fake_run) -> None:
    calls = fake_run["calls"]
    fake_run["fail_at"] = 1
    assert serve_cmd._build_local_web(tmp_path, "npm") is False
    assert [c[0] for c in calls] == [["npm", "install"]]


# ---------------------------------------------------------------------------
# _start_frontend
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_popen(monkeypatch) -> list:
    calls: list = []

    def _popen(cmd, **kw):
        calls.append((cmd, kw))
        return "proc"

    monkeypatch.setattr(_frontend.subprocess, "Popen", _popen)
    return calls


def test_start_frontend_local_build_copies_assets(tmp_path, web_cache, fake_popen) -> None:
    web = tmp_path / "web"
    app = web / ".next" / "standalone" / "packages" / "web"
    app.mkdir(parents=True)
    (app / "server.js").write_text("")
    (web / ".next" / "static").mkdir()
    (web / ".next" / "static" / "chunk.js").write_text("c")
    (web / "public").mkdir()
    (web / "public" / "logo.svg").write_text("l")

    proc = serve_cmd._start_frontend("node", 7400, 3100, local_web=web, host="0.0.0.0")

    assert proc == "proc"
    assert (app / ".next" / "static" / "chunk.js").read_text() == "c"
    assert (app / "public" / "logo.svg").read_text() == "l"
    [(cmd, kw)] = fake_popen
    assert cmd == ["node", str(app / "server.js")]
    assert kw["cwd"] == str(app)
    assert kw["env"]["REPOWISE_API_URL"] == "http://localhost:7400"
    assert kw["env"]["HOSTNAME"] == "0.0.0.0"
    assert kw["env"]["PORT"] == "3100"


def test_start_frontend_leaves_existing_static_alone(tmp_path, web_cache, fake_popen) -> None:
    web = tmp_path / "web"
    app = web / ".next" / "standalone" / "packages" / "web"
    (app / ".next" / "static").mkdir(parents=True)
    (app / "server.js").write_text("")
    (web / ".next" / "static").mkdir(parents=True)
    (web / ".next" / "static" / "new.js").write_text("n")
    serve_cmd._start_frontend("node", 7337, 3000, local_web=web)
    assert not (app / ".next" / "static" / "new.js").exists()


def test_start_frontend_falls_back_to_cache(tmp_path, web_cache, fake_popen) -> None:
    web = tmp_path / "web"  # no standalone bundle
    web.mkdir()
    web_cache.mkdir()
    (web_cache / "server.js").write_text("")
    assert serve_cmd._start_frontend("node", 7337, 3000, local_web=web) == "proc"
    [(cmd, kw)] = fake_popen
    assert cmd == ["node", str(web_cache / "server.js")]
    assert kw["cwd"] == str(web_cache)


def test_start_frontend_none_without_any_bundle(web_cache, fake_popen) -> None:
    assert serve_cmd._start_frontend("node", 7337, 3000) is None
    assert fake_popen == []


# ---------------------------------------------------------------------------
# serve_command branch order
# ---------------------------------------------------------------------------


class _Proc:
    def __init__(self, events: list) -> None:
        self.events = events

    def terminate(self) -> None:
        self.events.append("terminate")

    def wait(self, timeout=None) -> None:
        self.events.append(("wait", timeout))


@pytest.fixture
def serve(monkeypatch, tmp_path) -> dict:
    """Stub every side effect of `serve` and record the order of calls."""
    _own_env(monkeypatch, "REPOWISE_API_KEY")
    monkeypatch.setenv("REPOWISE_HOST", "127.0.0.1")
    monkeypatch.setenv("REPOWISE_DB_URL", "sqlite+aiosqlite:///:memory:")
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)

    st: dict = {
        "events": [],
        "node": "node",
        "npm": "npm",
        "node_major": 22,
        "local_web": None,
        "stale": False,
        "build_ok": True,
        "cached": False,
        "download_ok": False,
        "proc": True,
        "lock": None,
    }
    ev = st["events"]

    def rec(name, value=None):
        def _f(*a, **kw):
            ev.append((name, *a, *sorted(kw.items())))
            return st[value] if value else None

        return _f

    monkeypatch.setattr(_advisory, "_update_advisory_tick", lambda _a: None)
    monkeypatch.setattr(_advisory, "_start_update_advisory_loop", lambda _a: None)
    monkeypatch.setattr(_embedder, "_load_local_provider_config", rec("load_local"))
    monkeypatch.setattr(_embedder, "_setup_embedder", rec("setup_embedder"))

    def free_port(host, port, label):
        ev.append(("port", host, port, label))
        return port + 1

    monkeypatch.setattr(_ports, "_find_free_port", free_port)
    monkeypatch.setattr(_web_assets, "_node_available", lambda: st["node"])
    monkeypatch.setattr(_web_assets, "_npm_available", lambda: st["npm"])
    monkeypatch.setattr(_web_assets, "_node_major_version", lambda _n: st["node_major"])
    monkeypatch.setattr(_web_assets, "_find_local_web", rec("find_local", "local_web"))
    monkeypatch.setattr(_web_assets, "_local_build_is_stale", rec("stale", "stale"))
    monkeypatch.setattr(_web_assets, "_build_local_web", rec("build", "build_ok"))
    monkeypatch.setattr(_web_assets, "_web_is_cached", rec("cached", "cached"))
    monkeypatch.setattr(_web_assets, "_download_web", rec("download", "download_ok"))

    def start(node, port, ui_port, local_web=None, host=None):
        ev.append(("start", node, port, ui_port, local_web, host))
        return _Proc(ev) if st["proc"] else None

    monkeypatch.setattr(_frontend, "_start_frontend", start)

    def fake_uvicorn(app, **kw):
        lock = serve_cmd._serve_lock_path()
        if lock is not None:
            st["lock"] = json.loads(lock.read_text(encoding="utf-8"))
        ev.append(("uvicorn", app, *sorted(kw.items())))

    monkeypatch.setattr("uvicorn.run", fake_uvicorn)
    st["tmp"] = tmp_path
    return st


def _invoke(*args: str):
    return CliRunner().invoke(serve_cmd.serve_command, list(args))


def _names(events: list) -> list:
    return [e if isinstance(e, str) else e[0] for e in events]


def test_serve_api_only_order_and_uvicorn_args(serve) -> None:
    result = _invoke("--no-ui", "--port", "8000", "--workers", "2")
    assert result.exit_code == 0, result.output
    assert serve["events"] == [
        ("load_local",),
        ("setup_embedder",),
        ("port", "127.0.0.1", 8000, "API server"),
        (
            "uvicorn",
            "repowise.server.app:create_app",
            ("factory", True),
            ("host", "127.0.0.1"),
            ("log_level", "info"),
            ("port", 8001),
            ("workers", 2),
        ),
    ]
    assert serve["lock"]["port"] == 8001
    assert serve["lock"]["ui_port"] is None
    # The lock is removed once uvicorn returns.
    assert not (serve["tmp"] / ".repowise" / "serve.lock.json").exists()


def test_serve_without_node_skips_ui(serve) -> None:
    serve["node"] = None
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert "Node.js" in result.output
    assert _names(serve["events"]) == [
        "load_local",
        "setup_embedder",
        "port",
        "port",
        "uvicorn",
    ]
    assert serve["events"][3] == ("port", "127.0.0.1", 3000, "web UI")
    assert serve["lock"]["ui_port"] is None


def test_serve_old_node_skips_ui(serve) -> None:
    serve["node_major"] = 18
    result = _invoke()
    assert "too old" in result.output
    assert "start" not in _names(serve["events"])


def test_serve_unknown_node_version_still_tries_ui(serve) -> None:
    serve["node_major"] = None
    serve["cached"] = True
    _invoke()
    assert "start" in _names(serve["events"])


def test_serve_fresh_local_build_starts_it(serve, tmp_path) -> None:
    serve["local_web"] = tmp_path / "web"
    result = _invoke("--host", "0.0.0.0")
    assert result.exit_code == 0, result.output
    assert _names(serve["events"]) == [
        "load_local",
        "setup_embedder",
        "port",
        "port",
        "find_local",
        "stale",
        "start",
        "uvicorn",
        "terminate",
        "wait",
    ]
    assert serve["events"][6] == ("start", "node", 7338, 3001, tmp_path / "web", "0.0.0.0")
    assert serve["events"][-1] == ("wait", 5)
    assert serve["lock"]["ui_port"] == 3001
    assert serve["lock"]["url"] == "http://127.0.0.1:7338"


def test_serve_stale_local_build_rebuilds(serve, tmp_path) -> None:
    serve["local_web"] = tmp_path / "web"
    serve["stale"] = True
    _invoke()
    names = _names(serve["events"])
    assert names[4:8] == ["find_local", "stale", "build", "start"]
    assert serve["events"][6] == ("build", tmp_path / "web", "npm")
    assert serve["events"][7][4] == tmp_path / "web"


def test_serve_failed_rebuild_falls_back_to_cache(serve, tmp_path) -> None:
    serve["local_web"] = tmp_path / "web"
    serve["stale"] = True
    serve["build_ok"] = False
    serve["cached"] = True
    result = _invoke()
    assert "rebuild failed" in result.output
    names = _names(serve["events"])
    assert names[4:9] == ["find_local", "stale", "build", "cached", "start"]
    assert serve["events"][8][4] is None  # local_web dropped


def test_serve_stale_without_npm_downloads(serve, tmp_path) -> None:
    serve["local_web"] = tmp_path / "web"
    serve["stale"] = True
    serve["npm"] = None
    serve["download_ok"] = True
    result = _invoke()
    assert "npm" in result.output
    names = _names(serve["events"])
    assert names[4:9] == ["find_local", "stale", "cached", "download", "start"]
    assert serve["events"][8][4] is None


def test_serve_refresh_ui_skips_local_and_cache(serve) -> None:
    serve["download_ok"] = True
    _invoke("--refresh-ui")
    names = _names(serve["events"])
    assert "find_local" not in names and "cached" not in names
    assert names[4:6] == ["download", "start"]


def test_serve_no_assets_runs_api_only(serve) -> None:
    result = _invoke()
    assert "Could not get the web UI" in result.output
    assert "repowise serve --refresh-ui" in result.output
    assert "future release" not in result.output
    names = _names(serve["events"])
    assert names[4:] == ["find_local", "cached", "download", "uvicorn"]
    assert serve["lock"]["ui_port"] is None


def test_serve_frontend_that_fails_to_start(serve) -> None:
    serve["cached"] = True
    serve["proc"] = False
    result = _invoke()
    assert "Could not start the web UI" in result.output
    assert serve["lock"]["ui_port"] is None
    assert "terminate" not in _names(serve["events"])


def test_serve_missing_uvicorn_exits_1(serve, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    result = _invoke("--no-ui")
    assert result.exit_code == 1
    assert "uvicorn is not installed" in result.output
    assert serve["events"] == []


def test_serve_advisory_runs_only_on_a_terminal(serve, monkeypatch) -> None:
    ticks: list = []
    monkeypatch.setattr(_advisory, "_update_advisory_tick", lambda a: ticks.append("tick"))
    monkeypatch.setattr(_advisory, "_start_update_advisory_loop", lambda a: ticks.append("loop"))
    monkeypatch.setattr(type(_advisory.console), "is_terminal", property(lambda _s: False))
    _invoke("--no-ui")
    assert ticks == []
    monkeypatch.setattr(type(_advisory.console), "is_terminal", property(lambda _s: True))
    _invoke("--no-ui")
    assert ticks == ["tick", "loop"]


def test_serve_db_url_uses_posix_path(serve, monkeypatch, tmp_path) -> None:
    # Unrecorded pop: the autouse guard in tests/conftest.py restores it.
    os.environ.pop("REPOWISE_DB_URL", None)
    _invoke("--no-ui")
    expected = (tmp_path / ".repowise" / "wiki.db").as_posix()
    assert os.environ["REPOWISE_DB_URL"] == f"sqlite+aiosqlite:///{expected}"


def test_serve_no_db_url_without_local_store(serve, monkeypatch, tmp_path) -> None:
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    os.environ.pop("REPOWISE_DB_URL", None)
    result = _invoke("--no-ui")
    assert result.exit_code == 0, result.output
    assert "REPOWISE_DB_URL" not in os.environ
    assert serve["lock"] is None  # no index dir, no lockfile

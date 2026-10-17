"""Tests for Dart extension method calls resolved against typed receivers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _parse(tmp_path: Path, rel_path: str, source: str) -> ParsedFile:
    abs_path = tmp_path / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(source, encoding="utf-8")
    info = FileInfo(
        path=rel_path,
        abs_path=str(abs_path),
        language="dart",
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return parse_file(info, source.encode("utf-8"))


def test_dart_extension_method_called_on_param_local_and_field(tmp_path: Path) -> None:
    repo_src = """\
class Repo {
  void close() {}
}
extension RepoX on Repo {
  void flush() {}
}
"""
    app_src = """\
import 'repo.dart';
class App {
  final Repo rField = Repo();
  void run(Repo rParam) {
    rParam.flush();
    Repo rLocal = Repo();
    rLocal.flush();
    rField.flush();
  }
}
"""
    p_repo = _parse(tmp_path, "repo.dart", repo_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    for imp in p_app.imports:
        imp.resolved_file = "repo.dart"

    parsed = {"repo.dart": p_repo, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {"app.dart": {"repo.dart"}, "repo.dart": set()},
        repo_path=str(tmp_path),
    )
    edges = {
        (rc.line, rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run" and rc.callee_id.endswith("::flush")
    }
    assert edges == {
        (5, "repo.dart::RepoX::flush", "receiver_extension_import"),
        (7, "repo.dart::RepoX::flush", "receiver_extension_import"),
        (8, "repo.dart::RepoX::flush", "receiver_extension_import"),
    }


def test_dart_extension_on_ancestor_serves_subclass(tmp_path: Path) -> None:
    repo_src = """\
class Base {}
class Sub extends Base {}
extension BaseX on Base {
  void flush() {}
}
"""
    app_src = """\
import 'repo.dart';
class App {
  void run(Sub s) {
    s.flush();
  }
}
"""
    p_repo = _parse(tmp_path, "repo.dart", repo_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    for imp in p_app.imports:
        imp.resolved_file = "repo.dart"

    parsed = {"repo.dart": p_repo, "app.dart": p_app}
    heritage_parents = {"repo.dart::Sub": {"repo.dart::Base"}}
    resolver = CallResolver(
        parsed,
        {"app.dart": {"repo.dart"}, "repo.dart": set()},
        repo_path=str(tmp_path),
        heritage_parents=heritage_parents,
    )
    edges = [
        (rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
    ]
    assert edges == [("repo.dart::BaseX::flush", "receiver_extension_import")]


def test_dart_class_member_wins_over_extension_of_same_name(tmp_path: Path) -> None:
    repo_src = """\
class Repo {
  void flush() {}
}
extension RepoX on Repo {
  void flush() {}
}
"""
    app_src = """\
import 'repo.dart';
class App {
  void run(Repo r) {
    r.flush();
  }
}
"""
    p_repo = _parse(tmp_path, "repo.dart", repo_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    for imp in p_app.imports:
        imp.resolved_file = "repo.dart"

    parsed = {"repo.dart": p_repo, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {"app.dart": {"repo.dart"}, "repo.dart": set()},
        repo_path=str(tmp_path),
    )
    edges = [
        (rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
    ]
    assert edges == [("repo.dart::Repo::flush", "receiver_typed_import")]


def test_dart_extension_in_unimported_file_binds_nothing(tmp_path: Path) -> None:
    repo_src = """\
class Repo {}
extension RepoX on Repo {
  void flush() {}
}
"""
    app_src = """\
class App {
  void run(Repo r) {
    r.flush();
  }
}
"""
    p_repo = _parse(tmp_path, "repo.dart", repo_src)
    p_app = _parse(tmp_path, "app.dart", app_src)

    parsed = {"repo.dart": p_repo, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {"app.dart": set(), "repo.dart": set()},
        repo_path=str(tmp_path),
    )
    edges = [
        rc
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
    ]
    assert edges == []


def test_dart_two_imported_extensions_on_same_type_and_method_bind_nothing(
    tmp_path: Path,
) -> None:
    repo_src = """\
class Repo {}
extension RepoX on Repo {
  void flush() {}
}
"""
    repo2_src = """\
import 'repo.dart';
extension RepoY on Repo {
  void flush() {}
}
"""
    app_src = """\
import 'repo.dart';
import 'repo2.dart';
class App {
  void run(Repo r) {
    r.flush();
  }
}
"""
    p_repo = _parse(tmp_path, "repo.dart", repo_src)
    p_repo2 = _parse(tmp_path, "repo2.dart", repo2_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    p_repo2.imports[0].resolved_file = "repo.dart"
    p_app.imports[0].resolved_file = "repo.dart"
    p_app.imports[1].resolved_file = "repo2.dart"

    parsed = {"repo.dart": p_repo, "repo2.dart": p_repo2, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {
            "app.dart": {"repo.dart", "repo2.dart"},
            "repo2.dart": {"repo.dart"},
            "repo.dart": set(),
        },
        repo_path=str(tmp_path),
    )
    edges = [
        rc
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
    ]
    assert edges == []


def test_dart_single_visible_extension_resolves_when_unrelated_extension_exists(
    tmp_path: Path,
) -> None:
    repo_src = """\
class Repo {}
extension RepoX on Repo {
  void flush() {}
}
"""
    other_src = """\
import 'repo.dart';
extension RepoUnrelated on Repo {
  void flush() {}
}
"""
    app_src = """\
import 'repo.dart';
class App {
  void run(Repo r) {
    r.flush();
  }
}
"""
    p_repo = _parse(tmp_path, "repo.dart", repo_src)
    p_other = _parse(tmp_path, "other.dart", other_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    p_other.imports[0].resolved_file = "repo.dart"
    p_app.imports[0].resolved_file = "repo.dart"

    parsed = {"repo.dart": p_repo, "other.dart": p_other, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {
            "app.dart": {"repo.dart"},
            "other.dart": {"repo.dart"},
            "repo.dart": set(),
        },
        repo_path=str(tmp_path),
    )
    edges = [
        (rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
    ]
    assert edges == [("repo.dart::RepoX::flush", "receiver_extension_import")]


def test_dart_explicit_extension_invocation(tmp_path: Path) -> None:
    repo_src = """\
class Repo {}
extension RepoX on Repo {
  void flush() {}
}
"""
    app_src = """\
import 'repo.dart';
class App {
  void run(Repo r) {
    RepoX(r).flush();
  }
}
"""
    p_repo = _parse(tmp_path, "repo.dart", repo_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    for imp in p_app.imports:
        imp.resolved_file = "repo.dart"

    parsed = {"repo.dart": p_repo, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {"app.dart": {"repo.dart"}, "repo.dart": set()},
        repo_path=str(tmp_path),
    )
    edges = [
        (rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run" and rc.callee_id.endswith("::flush")
    ]
    assert edges == [("repo.dart::RepoX::flush", "return_type_import")]


def test_dart_multiple_extensions_targeting_different_receiver_types(
    tmp_path: Path,
) -> None:
    types_src = """\
class User {}
class Order {}
extension UserExt on User {
  void notifyUser() {}
}
extension OrderExt on Order {
  void cancelOrder() {}
}
"""
    app_src = """\
import 'types.dart';
class App {
  void run(User u, Order o) {
    u.notifyUser();
    o.cancelOrder();
    UserExt(u).notifyUser();
    OrderExt(o).cancelOrder();
  }
}
"""
    p_types = _parse(tmp_path, "types.dart", types_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    for imp in p_app.imports:
        imp.resolved_file = "types.dart"

    parsed = {"types.dart": p_types, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {"app.dart": {"types.dart"}, "types.dart": set()},
        repo_path=str(tmp_path),
    )
    run_calls = [
        (rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
        and (rc.callee_id.endswith("::notifyUser") or rc.callee_id.endswith("::cancelOrder"))
    ]
    assert set(run_calls) == {
        ("types.dart::UserExt::notifyUser", "receiver_extension_import"),
        ("types.dart::OrderExt::cancelOrder", "receiver_extension_import"),
        ("types.dart::UserExt::notifyUser", "return_type_import"),
        ("types.dart::OrderExt::cancelOrder", "return_type_import"),
    }


def test_dart_receiver_with_no_matching_extension(tmp_path: Path) -> None:
    types_src = """\
class User {}
extension UserExt on User {
  void notifyUser() {}
}
"""
    app_src = """\
import 'types.dart';
class Other {}
class App {
  void run(Other o, User u) {
    o.notifyUser();
    u.nonExistentMethod();
  }
}
"""
    p_types = _parse(tmp_path, "types.dart", types_src)
    p_app = _parse(tmp_path, "app.dart", app_src)
    for imp in p_app.imports:
        imp.resolved_file = "types.dart"

    parsed = {"types.dart": p_types, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {"app.dart": {"types.dart"}, "types.dart": set()},
        repo_path=str(tmp_path),
    )
    edges = [
        rc
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
    ]
    assert edges == []


def test_dart_extension_binds_nothing_when_receiver_type_ambiguous_across_repo(
    tmp_path: Path,
) -> None:
    foo1_src = "class Foo {}"
    foo2_src = "class Foo {}"
    ext_src = """\
import \x27foo1.dart\x27;
extension FooExt on Foo {
  void doWork() {}
}
"""
    app_src = """\
import \x27foo1.dart\x27;
import \x27ext.dart\x27;
class App {
  void run(Foo f) {
    f.doWork();
  }
}
"""
    p_foo1 = _parse(tmp_path, "foo1.dart", foo1_src)
    p_foo2 = _parse(tmp_path, "foo2.dart", foo2_src)
    p_ext = _parse(tmp_path, "ext.dart", ext_src)
    for imp in p_ext.imports:
        imp.resolved_file = "foo1.dart"
    p_app = _parse(tmp_path, "app.dart", app_src)
    for imp in p_app.imports:
        if "foo1" in imp.module_path:
            imp.resolved_file = "foo1.dart"
        elif "ext" in imp.module_path:
            imp.resolved_file = "ext.dart"

    parsed = {"foo1.dart": p_foo1, "foo2.dart": p_foo2, "ext.dart": p_ext, "app.dart": p_app}
    resolver = CallResolver(
        parsed,
        {
            "app.dart": {"foo1.dart", "ext.dart"},
            "ext.dart": {"foo1.dart"},
            "foo1.dart": set(),
            "foo2.dart": set(),
        },
        repo_path=str(tmp_path),
    )
    edges = [
        rc
        for rc in resolver.resolve_file("app.dart", p_app.calls)
        if rc.caller_id == "app.dart::App::run"
    ]
    assert edges == []

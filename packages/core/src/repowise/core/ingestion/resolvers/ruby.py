"""Ruby import resolution.

Resolution order, highest fidelity first:

1. ``require_relative`` / dot-leading paths — importer-relative join.
2. ``$LOAD_PATH`` convention probes — a gem puts ``lib/`` on the load
   path, so ``require 'sinatra/base'`` from anywhere in the repo means
   ``lib/sinatra/base.rb``; plain repo-root joins cover script-style
   layouts.
3. Stem lookup / suffix matching (legacy fuzzy fallbacks).
4. Rails / Zeitwerk autoload roots.
5. External: requires matching a Gemfile/gemspec dependency become a
   clearly-labelled gem node (``external:gem:<name>``); everything else
   keeps the plain external node.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .context import ResolverContext

# Gemfile:  gem 'rack', '~> 3.0'
_GEMFILE_GEM_RE = re.compile(r"^\s*gem\s+[\"']([A-Za-z0-9_.-]+)[\"']", re.MULTILINE)
# gemspec:  spec.add_dependency 'rack' / add_runtime_dependency / add_development_dependency
_GEMSPEC_DEP_RE = re.compile(
    r"\.add_(?:runtime_|development_)?dependency\s*\(?\s*[\"']([A-Za-z0-9_.-]+)[\"']"
)

# Ruby standard library require names (default + bundled gems shipping with
# the interpreter). Requires of these produce no node at all — same policy
# as the JDK namespaces for Java. First path segment is matched, so
# ``net/http`` and ``io/console`` are covered by their roots.
_RUBY_STDLIB = frozenset({
    "abbrev", "base64", "benchmark", "bigdecimal", "cgi", "coverage", "csv",
    "date", "delegate", "digest", "drb", "English", "english", "erb", "etc",
    "fcntl", "fiddle", "fileutils", "find", "forwardable", "getoptlong",
    "io", "ipaddr", "irb", "json", "logger", "minitest", "monitor", "mutex_m",
    "net", "nkf", "objspace", "observer", "open-uri", "open3", "openssl",
    "optparse", "ostruct", "pathname", "pp", "prettyprint", "prime", "pstore",
    "psych", "pty", "racc", "rake", "rbconfig", "rdoc", "readline", "resolv",
    "rexml", "rinda", "ripper", "rss", "rubygems", "securerandom", "set",
    "shellwords", "singleton", "socket", "stringio", "strscan", "syslog",
    "tempfile", "test", "time", "timeout", "tmpdir", "tsort", "un", "uri",
    "weakref", "yaml", "zlib",
})


def is_ruby_stdlib(module_path: str) -> bool:
    """True when the require names a Ruby standard-library module."""
    head = module_path.split("/", 1)[0]
    return head in _RUBY_STDLIB


def _scan_gem_metadata(ctx: ResolverContext) -> tuple[frozenset[str], tuple[str, ...]]:
    """Scan every Gemfile + *.gemspec once: dependency names + lib roots.

    Monorepos (sinatra: rack-protection/, sinatra-contrib/) declare each
    sub-gem's dependencies in its own gemspec — the walk covers them all.
    Every gemspec's directory contributes a ``<dir>/lib`` load-path root,
    exactly as ``gem build``/bundler put it on ``$LOAD_PATH``.
    """
    cached = getattr(ctx, "_ruby_gem_meta", None)
    if cached is not None:
        return cached
    names: set[str] = set()
    lib_roots: set[str] = set()
    if ctx.repo_path is not None:
        from repowise.core.fs_walk import iter_glob

        repo = ctx.repo_path.resolve()
        candidates: list = []
        try:
            candidates.extend(
                iter_glob(ctx.repo_path, "Gemfile", prune_nested_git=ctx.prune_nested_git)
            )
            candidates.extend(
                iter_glob(ctx.repo_path, "*.gemspec", prune_nested_git=ctx.prune_nested_git)
            )
        except OSError:
            pass
        for f in sorted(candidates):
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            names.update(_GEMFILE_GEM_RE.findall(text))
            names.update(_GEMSPEC_DEP_RE.findall(text))
            if f.name.endswith(".gemspec"):
                try:
                    rel_dir = f.parent.resolve().relative_to(repo).as_posix()
                except (OSError, ValueError):
                    continue
                lib_roots.add("lib" if rel_dir == "." else f"{rel_dir}/lib")
    result = (frozenset(names), tuple(sorted(lib_roots)))
    ctx._ruby_gem_meta = result  # type: ignore[attr-defined]
    return result


def _get_or_build_gem_names(ctx: ResolverContext) -> frozenset[str]:
    return _scan_gem_metadata(ctx)[0]


def _is_declared_gem(module_path: str, ctx: ResolverContext) -> bool:
    """True when the require names a Gemfile/gemspec dependency.

    ``require 'rack/protection'`` matches either the first path segment
    (gem ``rack``) or the dash-joined full path (gem ``rack-protection``).
    """
    gems = _get_or_build_gem_names(ctx)
    if not gems:
        return False
    head = module_path.split("/", 1)[0]
    return head in gems or module_path.replace("/", "-") in gems


def resolve_ruby_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Ruby require/require_relative to a repo-relative file path."""
    # require_relative uses paths relative to the current file
    if module_path.startswith("."):
        importer_dir = PurePosixPath(importer_path).parent
        candidate = (importer_dir / module_path).as_posix()
        # Try with .rb extension
        for suffix in (".rb", ""):
            full = f"{candidate}{suffix}"
            if full in ctx.path_set:
                return full

    # $LOAD_PATH convention: gems put lib/ on the path, so a plain
    # ``require 'sinatra/base'`` from ANY file in the repo means
    # ``lib/sinatra/base.rb``. Probe before any fuzzy fallback — these
    # joins are exact.
    if not module_path.startswith("."):
        # Repo-root lib/ + every sub-gem's lib/ (gemspec dirs) + root join.
        _gems, lib_roots = _scan_gem_metadata(ctx)
        bases = dict.fromkeys(("lib", *lib_roots, ""))
        for base in bases:
            candidate = f"{base}/{module_path}.rb" if base else f"{module_path}.rb"
            if candidate in ctx.path_set:
                return candidate

    # Standard-library requires produce no node — same policy as the JDK
    # namespace filter for Java. The exact probes above get first refusal
    # (the repo may BE the json gem), but stdlib names never reach the
    # fuzzy stem/suffix fallbacks where `require 'time'` would happily
    # false-match any time.rb in the repo.
    if not module_path.startswith(".") and is_ruby_stdlib(module_path):
        return None

    # Try stem lookup
    stem = PurePosixPath(module_path).stem.lower().replace("-", "_")
    result = ctx.stem_lookup(stem)
    if result and result.endswith(".rb"):
        return result

    # Try matching the path directly
    rb_name = f"{module_path}.rb"
    for p in ctx.sorted_paths:
        if p.endswith(rb_name) or PurePosixPath(p).name == PurePosixPath(rb_name).name:
            return p

    # Rails / Zeitwerk autoloading: ``require 'app/services/foo'`` style
    # paths can also be resolved by walking the rails index's
    # ``namespace_to_file`` map. Most Rails constant references are
    # require-less and surface through ``ctx.rails_lookup`` from the call
    # resolver / heritage extractor, not this function.
    from .ruby_rails import get_or_build_rails_index

    rails_index = get_or_build_rails_index(ctx)
    if rails_index is not None and not module_path.startswith("."):
        # Strip leading autoload-root prefixes (``app/services/foo`` →
        # ``foo`` lookup against namespace_to_file).
        normalised = module_path
        for root in rails_index.autoload_roots:
            prefix = f"{root}/"
            if normalised.startswith(prefix):
                normalised = normalised[len(prefix) :]
                break
        hit = rails_index.namespace_to_file.get(normalised)
        if hit:
            return hit

    # External: a require that matches a declared Gemfile/gemspec
    # dependency is a gem, labelled so the graph (and any reader of it)
    # can tell library dependencies from genuinely unresolved requires.
    if _is_declared_gem(module_path, ctx):
        return ctx.add_external_node(f"gem:{module_path}")
    return ctx.add_external_node(module_path)

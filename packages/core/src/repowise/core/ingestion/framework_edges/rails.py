"""Rails routes, ActiveRecord, ActionCable and other convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.

What the framework loads by name rather than by constant: routed
controllers, the files Rails and its tooling run from ``config/``, ActionCable
connections and channels, view helpers, Administrate dashboards, the mailbox
``ApplicationMailbox`` routes to, jobs a cron schedule names, a model's Pundit
policy, and the overlay module ``prepend_mod_with`` mixes in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..framework_facts.base import FrameworkFacts
from ..resolvers import ResolverContext
from ..resolvers.ruby_rails import constant_key, get_or_build_rails_index
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    anchor_edge,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers.ruby_rails import RailsIndex


_RAILS_RESOURCES_RE = re.compile(r"^(resources?)\s*\(?\s*:(\w+)")
_RAILS_GET_TO_RE = re.compile(
    r"\b(?:get|post|put|patch|delete|match|root)\b[^\n]*?(?:to:\s*|=>\s*)['\"]([\w/]+)#\w+['\"]"
)
# ``root 'home#index'``: the bare form names its target with no ``to:``.
_RAILS_ROOT_RE = re.compile(r"^root\s*\(?\s*['\"]([\w/]+)#\w+['\"]")
_RAILS_NAMESPACE_RE = re.compile(r"^namespace\s*\(?\s*:(\w+)")
_RAILS_SCOPE_RE = re.compile(r"^scope\b")
_RAILS_MODULE_OPT_RE = re.compile(r"\bmodule:\s*(?::(\w+)|['\"]([\w/]+)['\"])")
_RAILS_CONTROLLER_OPT_RE = re.compile(r"\bcontroller:\s*(?::(\w+)|['\"]([\w/]+)['\"])")
# ``controllers: { sessions: 'devise_overrides/sessions' }`` (Devise and kin).
_RAILS_CONTROLLERS_HASH_RE = re.compile(r"\bcontrollers:\s*\{([^}]*)\}")
_QUOTED_PATH_RE = re.compile(r"['\"]([\w/]+)['\"]")
_RAILS_DRAW_RE = re.compile(r"^draw\s*\(?\s*[:'\"](\w+)")
# A line that opens a block closed by ``end``: a trailing ``do`` (``do |x|``),
# or a compound statement at the start of the line (``if x`` opens one,
# ``foo if x`` is a modifier and opens nothing).
_DO_BLOCK_RE = re.compile(r"\bdo(?:\s*\|[^|]*\|)?$")
# The keyword must be followed by whitespace or end the line: ``module: :admin``
# continuing a split ``resources`` line is an option, not a block.
_OPENS_BLOCK_RE = re.compile(r"^(?:if|unless|case|while|until|begin|def|class|module)(?=\s|$)")
_RAILS_AR_RELATION_RE = re.compile(
    r"\b(?:belongs_to|has_many|has_one|has_and_belongs_to_many)\s+:(\w+)"
)
# ``Foo.prepend_mod_with('Foo')``, ``include_mod_with``, ``extend_mod_with``,
# and the argument-less ``prepend_mod`` forms, which name the caller's class.
_OVERLAY_MOD_RE = re.compile(
    r"\b(?:prepend|include|extend)_mod(?:_with\s*\(?\s*['\"]([\w:]+)['\"])?(?![\w(])"
)
# A cron schedule entry: ``class: 'Internal::TriggerScheduledItemsJob'``.
_SCHEDULE_CLASS_RE = re.compile(r"^\s*['\"]?class['\"]?\s*:\s*['\"]?([A-Z][\w:]*)", re.MULTILINE)
_SCHEDULE_FILES = ("schedule.yml", "sidekiq.yml", "sidekiq_cron.yml", "sidekiq_scheduler.yml")
# ``routing(/^reply/i => :reply)`` and ``routing(all: :default)``.
# The symbol must follow ``=>`` or a ``key:`` and a space, so the ``::`` of
# ``ReplyMailbox::Default`` is never read as one.
_MAILBOX_ROUTE_RE = re.compile(r"(?:=>\s*|\w:\s+):(\w+)")
_MAILBOX_ROUTER = "app/mailboxes/application_mailbox.rb"


def _rails_entry(path: str) -> bool:
    """Files Rails, ActionCable, ActionView or Administrate load with no constant naming them.

    ``config/`` is run by Rails and its tooling (environments, initializers,
    ``puma.rb``), and an overlay's ``config/initializers`` the same way. A
    channel is subscribed to by name from the client, every helper is mixed
    into the views, and Administrate finds a dashboard from its controller.
    """
    if not path.endswith(".rb"):
        return path == "config.ru"
    head, _, rest = path.partition("/")
    return (
        head == "config"
        or rest.startswith("config/initializers/")
        or path == "app/channels/application_cable/connection.rb"
        or (path.startswith("app/channels/") and path.endswith("_channel.rb"))
        or path.startswith("app/helpers/")
        or (path.startswith("app/dashboards/") and path.endswith("_dashboard.rb"))
        or path == _MAILBOX_ROUTER
    )


RAILS = FrameworkFacts(name="Rails", anchor="framework:rails", entry_match=_rails_entry)


def _singularize(word: str) -> str:
    """Very rough Rails inflector — sufficient for routes/AR lookups."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes") or word.endswith("zes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _plural_forms(word: str) -> tuple[str, ...]:
    """The controller names a singular ``resource :word`` may route to, likeliest first.

    Rails pluralizes the name, and its inflector leaves a word it reads as
    plural alone (``resource :notification_settings``) while ``status``
    becomes ``statuses``; a word ending in ``s`` tries both.
    """
    if word.endswith("s"):
        return (word, word + "es")
    if word.endswith("y") and word[-2:-1] not in ("a", "e", "i", "o", "u"):
        return (word[:-1] + "ies",)
    if word.endswith(("x", "z", "ch", "sh")):
        return (word + "es",)
    return (word + "s",)


def _camelize(word: str) -> str:
    return "".join(part.capitalize() for part in word.split("_"))


def _option(pattern: re.Pattern[str], line: str) -> tuple[str, ...]:
    """The path segments of a ``module:`` / ``controller:`` option, or ()."""
    m = pattern.search(line)
    return tuple((m.group(1) or m.group(2)).split("/")) if m else ()


@dataclass(frozen=True)
class _RouteFrame:
    """One open block in a routes file: the controller module segments it adds."""

    modules: tuple[str, ...] = ()


class _RoutesReader:
    """Reads ``config/routes.rb`` and the files it ``draw``s, tracking controller modules.

    Every block opener pushes a frame and every ``end`` pops one, so a nested
    ``resources ... do``, a ``member do`` or an ``if`` keeps the stack in step.
    ``namespace :x`` and ``scope module: :x`` add a controller module and a
    nested ``resources`` adds none, as in Rails. A ``module:`` option on
    ``resources`` applies to it and to what it nests; ``controller:`` names
    the controller outright. Ceiling: line-based, so a block opened and
    closed on one line with braces, or a route split over lines before its
    ``do``, is read as the line shows it.
    """

    def __init__(
        self,
        graph: nx.DiGraph,
        ctx: ResolverContext,
        path_set: set[str],
        parsed_files: dict[str, Any],
    ) -> None:
        self.graph = graph
        self.ctx = ctx
        self.path_set = path_set
        self.parsed_files = parsed_files
        self.count = 0
        self._drawn: set[tuple[str, tuple[str, ...]]] = set()
        self._reading: set[str] = {"config/routes.rb"}

    def read(self, path: str, stack: list[_RouteFrame]) -> None:
        parsed = self.parsed_files.get(path)
        text = read_text(parsed) if parsed is not None else ""
        # A ``controllers:`` hash names controllers by full path, often over several lines.
        for block in _RAILS_CONTROLLERS_HASH_RE.finditer(text):
            for m in _QUOTED_PATH_RE.finditer(block.group(1)):
                self._edge(path, m.group(1).split("/"))
        for raw_line in text.splitlines():
            line = raw_line.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line == "end" or line.startswith(("end ", "end.", "end)")):
                if stack:
                    stack.pop()
                continue
            self._line(path, line, stack)

    def _line(self, path: str, line: str, stack: list[_RouteFrame]) -> None:
        modules = [seg for frame in stack for seg in frame.modules]
        added: tuple[str, ...] = ()

        if ns := _RAILS_NAMESPACE_RE.match(line):
            added = (ns.group(1),)
        elif _RAILS_SCOPE_RE.match(line):
            added = _option(_RAILS_MODULE_OPT_RE, line)
        elif res := _RAILS_RESOURCES_RE.match(line):
            added = _option(_RAILS_MODULE_OPT_RE, line)
            controller = _option(_RAILS_CONTROLLER_OPT_RE, line)
            if controller:
                self._edge(path, [*modules, *added, *controller])
            elif res.group(1) == "resources":
                self._edge(path, [*modules, *added, res.group(2)])
            else:
                # ``resource :profile`` (singular) is served by ProfilesController.
                any(self._edge(path, [*modules, *added, n]) for n in _plural_forms(res.group(2)))
        elif draw := _RAILS_DRAW_RE.match(line):
            # One drawer may be drawn under several scopes, each routing to its
            # own modules; a file already being read is not drawn into itself.
            drawn = f"config/routes/{draw.group(1)}.rb"
            key = (drawn, tuple(modules))
            if drawn in self.path_set and key not in self._drawn and drawn not in self._reading:
                self._drawn.add(key)
                self.count += _add_edge_if_new(self.graph, path, drawn)
                self._reading.add(drawn)
                self.read(drawn, list(stack))
                self._reading.discard(drawn)

        for m in _RAILS_GET_TO_RE.finditer(line):
            self._edge(path, [*modules, *m.group(1).split("/")])
        if root := _RAILS_ROOT_RE.match(line):
            self._edge(path, [*modules, *root.group(1).split("/")])

        if _DO_BLOCK_RE.search(line) or _OPENS_BLOCK_RE.match(line):
            stack.append(_RouteFrame(added))

    def _edge(self, path: str, segments: list[str]) -> bool:
        """Link *path* to the controller *segments* name; True when it resolved."""
        target = _resolve_rails_controller(self.ctx, segments[:-1], segments[-1], self.path_set)
        if target:
            self.count += _add_edge_if_new(self.graph, path, target)
        return target is not None


def _add_rails_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = sum(anchor_edge(graph, RAILS, target) for target in RAILS.entry_files("", path_set))

    # ---- routes.rb → controller files ----
    if "config/routes.rb" in path_set:
        reader = _RoutesReader(graph, ctx, path_set, parsed_files)
        reader.read("config/routes.rb", [])
        count += reader.count

    index = get_or_build_rails_index(ctx)
    overlays = _overlay_keys(index) if index is not None else ()

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "ruby":
            continue
        text = read_text(parsed)
        if not text:
            continue
        # ---- ActiveRecord relationships: model → model ----
        if "/models/" in path or path.startswith("app/models/"):
            for m in _RAILS_AR_RELATION_RE.finditer(text):
                target = _resolve_rails_relation(ctx, m.group(1), path_set)
                if target and _add_edge_if_new(graph, path, target):
                    count += 1
        if index is None:
            continue
        # ---- Pundit finds ``ConversationPolicy`` from the model's class name ----
        if "/models/" in path and (own := index.file_to_constant.get(path)):
            policy = index.constant_to_file.get(f"{own}policy")
            if policy and policy in path_set:
                count += _add_edge_if_new(graph, path, policy)
        # ---- overlay: prepend_mod_with('Foo') → Enterprise::Foo ----
        if overlays and "_mod" in text:
            count += _overlay_edges(graph, index, overlays, path, text, path_set)
        # ---- ApplicationMailbox routing → the mailbox it names ----
        if path == _MAILBOX_ROUTER:
            for m in _MAILBOX_ROUTE_RE.finditer(text):
                target = index.resolve(f"{_camelize(m.group(1))}Mailbox", path)
                if target and target in path_set:
                    count += _add_edge_if_new(graph, path, target)

    # ---- cron schedules (sidekiq-cron, sidekiq-scheduler) → the jobs they run ----
    if index is not None:
        count += _scheduled_job_edges(graph, ctx, index, path_set)
    return count


def _overlay_keys(index: RailsIndex) -> tuple[str, ...]:
    """Constant keys of the overlay namespaces: ``enterprise/app/models`` gives ``enterprise``."""
    return tuple(
        sorted(
            {
                constant_key(root.split("/app/", 1)[0])
                for root in index.autoload_roots
                if "/app/" in root and not root.startswith("app/")
            }
        )
    )


def _overlay_edges(
    graph: nx.DiGraph,
    index: RailsIndex,
    overlays: tuple[str, ...],
    path: str,
    text: str,
    path_set: set[str],
) -> int:
    """``prepend_mod_with('Foo')`` mixes ``<Overlay>::Foo`` into ``Foo`` when the overlay defines it.

    The GitLab pattern Chatwoot also uses: an overlay tree (``enterprise/``,
    ``ee/``) whose ``app/`` defines ``Enterprise::Foo``, mixed in by name from
    the base class at load time. The argument-less forms name the caller's
    own class, taken from the file's Zeitwerk constant.
    """
    count = 0
    for m in _OVERLAY_MOD_RE.finditer(text):
        name = constant_key(m.group(1)) if m.group(1) else index.file_to_constant.get(path)
        if not name:
            continue
        for overlay in overlays:
            target = index.constant_to_file.get(f"{overlay}/{name}")
            if target and target in path_set:
                count += _add_edge_if_new(graph, path, target)
    return count


def _scheduled_job_edges(
    graph: nx.DiGraph, ctx: ResolverContext, index: RailsIndex, path_set: set[str]
) -> int:
    """Anchor each job a ``config/*.yml`` cron schedule names by ``class:``."""
    if ctx.repo_path is None:
        return 0
    count = 0
    for name in _SCHEDULE_FILES:
        try:
            text = (ctx.repo_path / "config" / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _SCHEDULE_CLASS_RE.finditer(text):
            target = index.resolve(f"::{m.group(1)}")
            if target and target in path_set:
                count += anchor_edge(graph, RAILS, target)
    return count


def _resolve_rails_controller(
    ctx: ResolverContext, namespace_stack: list[str], resource: str, path_set: set[str]
) -> str | None:
    """`resources :users` (inside `namespace :admin do`) or `to: "admin/users#index"` → controller path."""
    name = resource if resource.endswith("_controller") else f"{resource}_controller"
    expected = "app/controllers/" + "/".join([*namespace_stack, name]) + ".rb"
    if expected in path_set:
        return expected
    # A route names its controller from the top level, so resolve it that way:
    # ``lookup``'s last-segment stem fallback would bind
    # ``Admin::UsersController`` to a top-level ``UsersController``.
    index = get_or_build_rails_index(ctx)
    if index is None:
        return None
    return index.resolve("::" + "::".join(_camelize(seg) for seg in [*namespace_stack, name]))


def _resolve_rails_relation(
    ctx: ResolverContext, assoc_name: str, path_set: set[str]
) -> str | None:
    """`belongs_to :user` / `has_many :orders` → model file."""
    singular = _singularize(assoc_name)
    expected = f"app/models/{singular}.rb"
    if expected in path_set:
        return expected
    return ctx.rails_lookup(_camelize(singular))


class _RailsHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return dctx.uses("Rails")

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_rails_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_RailsHandler()]

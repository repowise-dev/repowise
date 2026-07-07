import * as vscode from "vscode";
import * as path from "node:path";
import type { RepowiseContext } from "../../core/context";

/**
 * One node in a Repowise activity-bar tree. Providers build a plain node tree
 * and the base turns each into a `TreeItem`. Optional fields carry `| undefined`
 * so nodes compose cleanly under `exactOptionalPropertyTypes`. Group nodes
 * either hold their children inline (`children`) or resolve them on expansion
 * (`loadChildren`); leaves carry neither.
 */
export interface RepoTreeNode {
  key: string;
  label: string;
  description?: string | undefined;
  tooltip?: string | vscode.MarkdownString | undefined;
  icon?: vscode.ThemeIcon | undefined;
  collapsibleState: vscode.TreeItemCollapsibleState;
  command?: vscode.Command | undefined;
  children?: RepoTreeNode[] | undefined;
  loadChildren?: (() => Promise<RepoTreeNode[]>) | undefined;
}

/**
 * Base for the five data-backed tree views. Handles the shared lifecycle so
 * each subclass only supplies `loadRoots`: lazy root fetch on first ask (VS
 * Code calls `getChildren` only when the view is visible), an in-memory root
 * cache cleared on refresh, per-provider index-change watching wired lazily
 * (never during activate), and a view badge surfaced through an event because
 * badges live on the `TreeView`, not on items.
 */
export abstract class RepowiseTreeProvider
  implements vscode.TreeDataProvider<RepoTreeNode>, vscode.Disposable
{
  private readonly changeEmitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changeEmitter.event;

  private readonly badgeEmitter = new vscode.EventEmitter<
    vscode.ViewBadge | undefined
  >();
  /** Fires the badge to set on the owning `TreeView` (undefined clears it). */
  readonly onDidChangeBadge = this.badgeEmitter.event;

  private rootsPromise: Promise<RepoTreeNode[]> | null = null;
  private freshnessSub: vscode.Disposable | null = null;

  /** Human label used only in debug log lines. */
  protected abstract readonly name: string;

  constructor(protected readonly ctx: RepowiseContext) {}

  /** Builds the root nodes for a resolved repo. Throws are caught upstream. */
  protected abstract loadRoots(repoId: string): Promise<RepoTreeNode[]>;

  /**
   * Public window onto `loadRoots` for composite providers that nest this
   * provider's roots under a section of their own tree. Bypasses this
   * provider's root cache and watcher on purpose: the composite owns both.
   */
  rootsFor(repoId: string): Promise<RepoTreeNode[]> {
    return this.loadRoots(repoId);
  }

  getTreeItem(node: RepoTreeNode): vscode.TreeItem {
    const item = new vscode.TreeItem(node.label, node.collapsibleState);
    if (node.description !== undefined) item.description = node.description;
    if (node.tooltip !== undefined) item.tooltip = node.tooltip;
    if (node.icon !== undefined) item.iconPath = node.icon;
    if (node.command !== undefined) item.command = node.command;
    return item;
  }

  async getChildren(node?: RepoTreeNode): Promise<RepoTreeNode[]> {
    if (node) {
      if (node.children) return node.children;
      if (node.loadChildren) {
        node.children = await this.guard(node.loadChildren);
        return node.children;
      }
      return [];
    }
    if (!this.rootsPromise) this.rootsPromise = this.computeRoots();
    return this.rootsPromise;
  }

  /** Drops the cached roots and re-asks the tree; data refetches on next ask. */
  refresh(): void {
    this.rootsPromise = null;
    this.changeEmitter.fire();
  }

  private async computeRoots(): Promise<RepoTreeNode[]> {
    this.ensureWatching();
    const repoId = this.ctx.repoId;
    // Views are gated to the `ready` state, but a state flip can race a pending
    // ask; render empty rather than hitting a null repo.
    if (!repoId) return [];
    return this.guard(() => this.loadRoots(repoId));
  }

  /** Runs a loader, turning any failure into a single "could not load" node. */
  private async guard(
    loader: () => Promise<RepoTreeNode[]>,
  ): Promise<RepoTreeNode[]> {
    try {
      return await loader();
    } catch (err) {
      this.ctx.log.debug(`${this.name} tree load failed: ${String(err)}`);
      return [errorNode()];
    }
  }

  /**
   * Subscribes to index changes on first data ask (not at registration, so a
   * cold start does no filesystem watching). An index rebuild re-resolves the
   * repo record (fresh `head_commit` => fresh cache tag) then refreshes.
   */
  private ensureWatching(): void {
    if (this.freshnessSub) return;
    const watcher = this.ctx.events();
    if (!watcher) return;
    this.freshnessSub = watcher.onDidChange(async (kind) => {
      if (kind !== "indexChanged") return;
      await this.ctx.refreshRepo();
      this.refresh();
    });
  }

  /** Fires a badge with the given count, or clears it when the count is zero. */
  protected setBadge(value: number, tooltip: string): void {
    this.badgeEmitter.fire(value > 0 ? { value, tooltip } : undefined);
  }

  /**
   * Reads a value from the shared cache keyed by (repoId, key, head_commit),
   * fetching and storing on a miss. The head commit is the freshness tag: an
   * index rebuild changes it and the old entry is simply never read again.
   */
  protected async cached<T>(key: string, loader: () => Promise<T>): Promise<T> {
    const repoId = this.ctx.repoId;
    if (!repoId) return loader();
    const tag = this.ctx.repo?.head_commit ?? "";
    const hit = this.ctx.cache.get<T>(repoId, key, tag);
    if (hit !== undefined) return hit;
    const value = await loader();
    this.ctx.cache.set(repoId, key, tag, value);
    return value;
  }

  /** Section root whose children resolve lazily when the section expands. */
  protected section(
    key: string,
    label: string,
    loader: () => Promise<RepoTreeNode[]>,
  ): RepoTreeNode {
    return {
      key,
      label,
      collapsibleState: vscode.TreeItemCollapsibleState.Expanded,
      loadChildren: loader,
    };
  }

  /** A friendly single leaf for empty results ("No findings", etc). */
  protected messageNode(label: string, icon = "info"): RepoTreeNode {
    return {
      key: `msg:${label}`,
      label,
      icon: new vscode.ThemeIcon(icon),
      collapsibleState: vscode.TreeItemCollapsibleState.None,
    };
  }

  dispose(): void {
    this.freshnessSub?.dispose();
    this.changeEmitter.dispose();
    this.badgeEmitter.dispose();
  }
}

/** The single node rendered when a root or section fetch fails. */
export function errorNode(): RepoTreeNode {
  return {
    key: "error",
    label: "Could not load (see log)",
    icon: new vscode.ThemeIcon("warning"),
    collapsibleState: vscode.TreeItemCollapsibleState.None,
  };
}

/**
 * Creates the `TreeView`, wires the badge onto it, and refreshes the provider
 * whenever the extension state settles (entering `ready` loads, leaving it
 * clears). Returns a disposable owning the view and the provider.
 */
export function mountView(
  ctx: RepowiseContext,
  viewId: string,
  provider: RepowiseTreeProvider,
): vscode.Disposable {
  const treeView = vscode.window.createTreeView(viewId, {
    treeDataProvider: provider,
  });
  const subs: vscode.Disposable[] = [
    treeView,
    provider,
    provider.onDidChangeBadge((badge) => {
      treeView.badge = badge;
    }),
    ctx.onDidChangeExtensionState(() => provider.refresh()),
  ];
  return vscode.Disposable.from(...subs);
}

/** Last path segment of a repo-relative, forward-slash server path. */
export function baseName(relPath: string): string {
  return path.posix.basename(relPath);
}

/**
 * A `vscode.open` command for a repo-relative path, selecting `line` (1-based)
 * when known. Server paths are forward-slash and repo-relative, so they join
 * onto the local repo root.
 */
export function openFileCommand(
  ctx: RepowiseContext,
  relPath: string,
  line?: number | null,
): vscode.Command {
  const uri = vscode.Uri.file(
    path.join(ctx.workspace.repoRoot ?? "", relPath),
  );
  const args: unknown[] = [uri];
  if (line != null) {
    const zero = Math.max(0, line - 1);
    const options: vscode.TextDocumentShowOptions = {
      selection: new vscode.Range(zero, 0, zero, 0),
    };
    args.push(options);
  }
  return { command: "vscode.open", title: "Open File", arguments: args };
}

/** Title-cases a snake_case type label, e.g. `extract_method` => `Extract Method`. */
export function humanizeType(type: string): string {
  return type
    .split("_")
    .filter((w) => w.length > 0)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Truncates to `max` characters, appending an ellipsis when cut. */
export function truncate(text: string, max = 80): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

import * as vscode from "vscode";
import { Views } from "../../constants";
import type { RepowiseContext } from "../../core/context";
import { DeadCodeTreeProvider } from "./deadCode";
import { HealthTreeProvider } from "./health";
import { HotspotsTreeProvider } from "./hotspots";
import { mountView, RepowiseTreeProvider, type RepoTreeNode } from "./shared";

/**
 * The single Findings view: Health, Hotspots, Ownership, and Dead Code as
 * sections of one tree instead of four stacked sidebar views, so the sidebar
 * leads with the Home view rather than a wall of section headers. The section
 * providers are never mounted; this composite calls their loaders directly and
 * owns the one root cache, freshness watcher, and badge (failing health files,
 * the actionable count; dead-code totals would be four-digit badge noise).
 */
class FindingsTreeProvider extends RepowiseTreeProvider {
  protected readonly name = "Findings";

  private readonly health = new HealthTreeProvider(this.ctx);
  private readonly hotspots = new HotspotsTreeProvider(this.ctx);
  private readonly deadCode = new DeadCodeTreeProvider(this.ctx);
  private readonly badgeSub: vscode.Disposable;

  constructor(ctx: RepowiseContext) {
    super(ctx);
    // The health loader computes the failing-file count as its badge; re-fire
    // it as this view's badge since the health provider itself is unmounted.
    this.badgeSub = this.health.onDidChangeBadge((badge) => {
      if (badge) this.setBadge(badge.value, badge.tooltip ?? "");
      else this.setBadge(0, "");
    });
  }

  protected loadRoots(repoId: string): Promise<RepoTreeNode[]> {
    return Promise.resolve([
      this.category("health", "Health", "pulse", true, () =>
        this.health.rootsFor(repoId),
      ),
      this.category("hotspots", "Hotspots", "flame", false, () =>
        this.hotspots.loadHotspots(),
      ),
      this.category("ownership", "Ownership", "organization", false, () =>
        this.hotspots.loadOwnership(),
      ),
      this.category("deadcode", "Dead Code", "trash", false, () =>
        this.deadCode.rootsFor(repoId),
      ),
    ]);
  }

  private category(
    key: string,
    label: string,
    icon: string,
    expanded: boolean,
    loader: () => Promise<RepoTreeNode[]>,
  ): RepoTreeNode {
    return {
      key: `cat:${key}`,
      label,
      icon: new vscode.ThemeIcon(icon),
      collapsibleState: expanded
        ? vscode.TreeItemCollapsibleState.Expanded
        : vscode.TreeItemCollapsibleState.Collapsed,
      loadChildren: loader,
    };
  }

  override dispose(): void {
    this.badgeSub.dispose();
    this.health.dispose();
    this.hotspots.dispose();
    this.deadCode.dispose();
    super.dispose();
  }
}

/** Registers the consolidated Findings tree view. */
export function registerFindingsTree(ctx: RepowiseContext): vscode.Disposable {
  return mountView(ctx, Views.findings, new FindingsTreeProvider(ctx));
}

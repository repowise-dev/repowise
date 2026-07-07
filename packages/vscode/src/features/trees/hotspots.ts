import * as vscode from "vscode";
import { getHotspotsPage, getOwnershipPage } from "@repowise-dev/api-client/git";
import type {
  HotspotResponse,
  OwnershipEntry,
} from "@repowise-dev/api-client/types";
import {
  baseName,
  openFileCommand,
  RepowiseTreeProvider,
  type RepoTreeNode,
} from "./shared";

const HOTSPOTS_KEY = "hotspots:page";
const OWNERSHIP_KEY = "ownership:page";

/**
 * Hotspots & Ownership view: two section roots, each fetched lazily when its
 * section expands (so opening the view fetches neither until its section is
 * shown). Hotspots are the highest-churn files; Ownership rolls up who owns
 * each module and flags single-owner silos.
 */
export class HotspotsTreeProvider extends RepowiseTreeProvider {
  protected readonly name = "Hotspots";

  protected loadRoots(): Promise<RepoTreeNode[]> {
    return Promise.resolve([
      this.section("hotspots", "Hotspots", () => this.loadHotspots()),
      this.section("ownership", "Ownership", () => this.loadOwnership()),
    ]);
  }

  /** Public: the Findings composite mounts these as its own sections. */
  async loadHotspots(): Promise<RepoTreeNode[]> {
    const repoId = this.ctx.repoId;
    if (!repoId) return [];
    const page = await this.cached(HOTSPOTS_KEY, () =>
      getHotspotsPage(repoId, { limit: 50 }),
    );
    if (page.items.length === 0) return [this.messageNode("No hotspots")];
    return page.items.map((row) => this.hotspotNode(row));
  }

  private hotspotNode(row: HotspotResponse): RepoTreeNode {
    const owner = row.primary_owner ?? "unknown";
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown(`\`${row.file_path}\`\n\n`);
    tooltip.appendMarkdown(`- Churn percentile: ${Math.round(row.churn_percentile)}\n`);
    tooltip.appendMarkdown(`- Bus factor: ${row.bus_factor}\n`);
    tooltip.appendMarkdown(`- Contributors: ${row.contributor_count}\n`);
    return {
      key: `hotspot:${row.file_path}`,
      label: baseName(row.file_path),
      description: `${row.commit_count_90d} commits · ${owner}`,
      tooltip,
      icon: new vscode.ThemeIcon("flame"),
      collapsibleState: vscode.TreeItemCollapsibleState.None,
      command: openFileCommand(this.ctx, row.file_path),
    };
  }

  async loadOwnership(): Promise<RepoTreeNode[]> {
    const repoId = this.ctx.repoId;
    if (!repoId) return [];
    const page = await this.cached(OWNERSHIP_KEY, () =>
      getOwnershipPage(repoId, "module", { limit: 200 }),
    );
    if (page.items.length === 0) return [this.messageNode("No ownership data")];
    return page.items.map((row) => this.ownershipNode(row));
  }

  private ownershipNode(row: OwnershipEntry): RepoTreeNode {
    const owner = row.primary_owner ?? "unknown";
    const pct = row.owner_pct == null ? "-" : `${Math.round(row.owner_pct)}%`;
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown(`\`${row.module_path}\`\n\n`);
    tooltip.appendMarkdown(`- Primary owner: ${owner} (${pct})\n`);
    tooltip.appendMarkdown(`- Files: ${row.file_count}\n`);
    if (row.is_silo) tooltip.appendMarkdown(`- Single-owner silo\n`);
    return {
      key: `owner:${row.module_path}`,
      label: row.module_path,
      description: `${owner} ${pct}`,
      tooltip,
      icon: row.is_silo ? new vscode.ThemeIcon("warning") : undefined,
      collapsibleState: vscode.TreeItemCollapsibleState.None,
    };
  }
}

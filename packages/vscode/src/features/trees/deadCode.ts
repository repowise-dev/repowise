import * as vscode from "vscode";
import { getDeadCodeSummary, listDeadCode } from "@repowise-dev/api-client/dead-code";
import type { DeadCodeFindingResponse } from "@repowise-dev/api-client/types";
import { Views } from "../../constants";
import type { RepowiseContext } from "../../core/context";
import {
  baseName,
  mountView,
  openFileCommand,
  RepowiseTreeProvider,
  type RepoTreeNode,
} from "./shared";

const FINDINGS_KEY = "deadcode:findings";
const SUMMARY_KEY = "deadcode:summary";

/** Confidence tiers, most-confident first, with their inclusive lower bounds. */
const TIERS: Array<{ key: string; label: string; min: number }> = [
  { key: "high", label: "High", min: 0.75 },
  { key: "medium", label: "Medium", min: 0.5 },
  { key: "low", label: "Low", min: 0 },
];

/**
 * Dead Code view: findings grouped into High / Medium / Low confidence tiers.
 * Empty tiers are dropped. Findings use `start_line`/`end_line` (distinct from
 * the health findings' `line_start`/`line_end`); a click jumps to `start_line`
 * when present. The badge is the summary's total finding count.
 */
class DeadCodeTreeProvider extends RepowiseTreeProvider {
  protected readonly name = "Dead Code";

  protected async loadRoots(repoId: string): Promise<RepoTreeNode[]> {
    const findings = await this.cached(FINDINGS_KEY, () =>
      listDeadCode(repoId, { limit: 500 }),
    );
    void this.loadBadge(repoId);

    if (findings.length === 0) return [this.messageNode("No dead code")];

    const roots: RepoTreeNode[] = [];
    for (const tier of TIERS) {
      const rows = findings.filter((f) => tierOf(f.confidence) === tier.key);
      if (rows.length === 0) continue;
      roots.push({
        key: `tier:${tier.key}`,
        label: tier.label,
        description: `${rows.length}`,
        collapsibleState: vscode.TreeItemCollapsibleState.Expanded,
        children: rows.map((row) => this.findingNode(row)),
      });
    }
    return roots;
  }

  private findingNode(row: DeadCodeFindingResponse): RepoTreeNode {
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown(`\`${row.file_path}\`\n\n`);
    tooltip.appendMarkdown(`- Kind: ${row.kind}\n`);
    tooltip.appendMarkdown(`- Confidence: ${row.confidence.toFixed(2)}\n`);
    tooltip.appendMarkdown(`- ${row.reason}\n`);
    return {
      key: `dead:${row.id}`,
      label: row.symbol_name ?? baseName(row.file_path),
      description: `${row.kind} · ${row.lines} lines`,
      tooltip,
      collapsibleState: vscode.TreeItemCollapsibleState.None,
      command: openFileCommand(this.ctx, row.file_path, row.start_line),
    };
  }

  private async loadBadge(repoId: string): Promise<void> {
    try {
      const summary = await this.cached(SUMMARY_KEY, () =>
        getDeadCodeSummary(repoId),
      );
      this.setBadge(summary.total_findings, `${summary.total_findings} dead-code findings`);
    } catch (err) {
      this.ctx.log.debug(`Dead Code badge load failed: ${String(err)}`);
    }
  }
}

/** Maps a 0-1 confidence to its tier key. */
function tierOf(confidence: number): string {
  for (const tier of TIERS) {
    if (confidence >= tier.min) return tier.key;
  }
  return "low";
}

/** Registers the Dead Code tree view. */
export function registerDeadCodeTree(ctx: RepowiseContext): vscode.Disposable {
  return mountView(ctx, Views.deadCode, new DeadCodeTreeProvider(ctx));
}

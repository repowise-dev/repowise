import * as vscode from "vscode";
import { listDecisions } from "@repowise-dev/api-client/decisions";
import type { DecisionRecordResponse } from "@repowise-dev/api-client/types";
import { Views } from "../../constants";
import type { RepowiseContext } from "../../core/context";
import {
  mountView,
  openFileCommand,
  RepowiseTreeProvider,
  truncate,
  type RepoTreeNode,
} from "./shared";

const DECISIONS_KEY = "decisions:all";

/** Status display order: accepted/active first, retired states last, else A-Z. */
const STATUS_RANK: Record<string, number> = {
  accepted: 0,
  active: 1,
  proposed: 2,
  deprecated: 4,
  superseded: 5,
};

/**
 * Decisions view: architectural decision records grouped by status, accepted
 * first. A record with a backing `evidence_file` opens it on click (at
 * `evidence_line`, 1-based, when present); records without evidence are not
 * clickable. No badge.
 */
class DecisionsTreeProvider extends RepowiseTreeProvider {
  protected readonly name = "Decisions";

  protected async loadRoots(repoId: string): Promise<RepoTreeNode[]> {
    // Same key and limit as the hover feature's decision lookup, so whichever
    // fetches first serves both.
    const decisions = await this.cached(DECISIONS_KEY, () =>
      listDecisions(repoId, { limit: 500 }),
    );
    if (decisions.length === 0) return [this.messageNode("No decisions")];

    const byStatus = new Map<string, DecisionRecordResponse[]>();
    for (const record of decisions) {
      const bucket = byStatus.get(record.status);
      if (bucket) bucket.push(record);
      else byStatus.set(record.status, [record]);
    }

    return Array.from(byStatus.keys())
      .sort(compareStatus)
      .map((status) => {
        const rows = byStatus.get(status) ?? [];
        return {
          key: `status:${status}`,
          label: capitalize(status),
          description: `${rows.length}`,
          collapsibleState: vscode.TreeItemCollapsibleState.Expanded,
          children: rows.map((row) => this.decisionNode(row)),
        };
      });
  }

  private decisionNode(row: DecisionRecordResponse): RepoTreeNode {
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown(`**Decision:** ${row.decision}\n\n`);
    tooltip.appendMarkdown(`**Rationale:** ${row.rationale}\n`);
    return {
      key: `decision:${row.id}`,
      label: truncate(row.title),
      description: row.source,
      tooltip,
      icon: new vscode.ThemeIcon("law"),
      collapsibleState: vscode.TreeItemCollapsibleState.None,
      command: row.evidence_file
        ? openFileCommand(this.ctx, row.evidence_file, row.evidence_line)
        : undefined,
    };
  }
}

/** Orders two status keys by preferred rank, falling back to alphabetical. */
function compareStatus(a: string, b: string): number {
  const ra = STATUS_RANK[a] ?? 3;
  const rb = STATUS_RANK[b] ?? 3;
  return ra === rb ? a.localeCompare(b) : ra - rb;
}

/** Upper-cases the first letter for a status label. */
function capitalize(text: string): string {
  return text.length === 0 ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

/** Registers the Decisions tree view. */
export function registerDecisionsTree(ctx: RepowiseContext): vscode.Disposable {
  return mountView(ctx, Views.decisions, new DecisionsTreeProvider(ctx));
}

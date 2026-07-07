import * as vscode from "vscode";
import { listHealthFiles } from "@repowise-dev/api-client/code-health";
import type { HealthFileMetric } from "@repowise-dev/api-client/code-health";
import {
  baseName,
  openFileCommand,
  RepowiseTreeProvider,
  type RepoTreeNode,
} from "./shared";

// Distinct from the bulk per-file score map cached under "health:files": this
// entry holds the worst-100 response object, not the full-repo score map.
const FILES_KEY = "health:worst";
const FAILING_KEY = "health:failing";

/** Formats a 0-10 health score, or a dash when the score is absent. */
function score(value: number | null | undefined): string {
  return value == null ? "-" : value.toFixed(1);
}

/**
 * Health view: the 100 lowest-scoring files, grouped by module with worst-first
 * children. The list arrives sorted ascending by score, so iterating it in
 * order yields both worst-first files and worst-first modules for free. The
 * badge counts failing files (a separate one-row query for its `total`).
 */
export class HealthTreeProvider extends RepowiseTreeProvider {
  protected readonly name = "Health";

  protected async loadRoots(repoId: string): Promise<RepoTreeNode[]> {
    const files = await this.cached(FILES_KEY, () =>
      listHealthFiles(repoId, { sort: "score", order: "asc", limit: 100 }),
    );
    void this.loadBadge(repoId);

    if (files.files.length === 0) return [this.messageNode("No health data")];

    const byModule = new Map<string, HealthFileMetric[]>();
    for (const file of files.files) {
      const module = file.module ?? "(no module)";
      const bucket = byModule.get(module);
      if (bucket) bucket.push(file);
      else byModule.set(module, [file]);
    }

    return Array.from(byModule, ([module, rows]) => ({
      key: `mod:${module}`,
      label: module,
      description: `${rows.length} ${rows.length === 1 ? "file" : "files"}`,
      collapsibleState: vscode.TreeItemCollapsibleState.Collapsed,
      children: rows.map((row) => this.fileNode(row)),
    }));
  }

  private fileNode(row: HealthFileMetric): RepoTreeNode {
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown(`\`${row.file_path}\`\n\n`);
    tooltip.appendMarkdown(`- Defect risk: ${score(row.defect_score ?? row.score)}\n`);
    tooltip.appendMarkdown(`- Maintainability: ${score(row.maintainability_score)}\n`);
    tooltip.appendMarkdown(`- Performance: ${score(row.performance_score)}\n`);
    return {
      key: `file:${row.file_path}`,
      label: baseName(row.file_path),
      description: score(row.defect_score ?? row.score),
      tooltip,
      collapsibleState: vscode.TreeItemCollapsibleState.None,
      command: openFileCommand(this.ctx, row.file_path),
    };
  }

  private async loadBadge(repoId: string): Promise<void> {
    try {
      const failing = await this.cached(FAILING_KEY, () =>
        listHealthFiles(repoId, { only_failing: true, limit: 1 }),
      );
      this.setBadge(failing.total, `${failing.total} failing files`);
    } catch (err) {
      this.ctx.log.debug(`Health badge load failed: ${String(err)}`);
    }
  }
}

import * as vscode from "vscode";
import { ApiClientError } from "@repowise-dev/api-client";
import { getRiskRange } from "@repowise-dev/api-client/risk";
import type { RiskRangeResponse } from "@repowise-dev/api-client/risk";
import { CONFIG_SECTION, Commands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { getCurrentBranchName } from "../core/gitApi";

/** Virtual-document scheme the rendered risk report is served on. */
const RISK_SCHEME = "repowise-risk";
/** Fixed virtual-document path; its basename becomes the preview title. */
const RISK_URI = vscode.Uri.parse(`${RISK_SCHEME}:/Branch Risk.md`);

/** Human-readable names for the raw change features the endpoint returns. */
const FEATURE_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["la", "Lines added"],
  ["ld", "Lines deleted"],
  ["nf", "Files changed"],
  ["nd", "Directories changed"],
  ["ns", "Subsystems changed"],
  ["entropy", "Change entropy"],
  ["exp", "Author experience"],
];

/**
 * Scores the working branch against a base and renders the result as a markdown
 * preview. Bound to the palette command and the SCM title button (both call
 * `Commands.checkBranchRisk`). No caching: risk reflects the working HEAD, so
 * each invocation fetches fresh.
 */
export function registerBranchRisk(ctx: RepowiseContext): vscode.Disposable {
  let content = "";
  const changeEmitter = new vscode.EventEmitter<vscode.Uri>();

  const provider: vscode.TextDocumentContentProvider = {
    onDidChange: changeEmitter.event,
    provideTextDocumentContent: () => content,
  };

  async function run(): Promise<void> {
    if (ctx.getExtensionState() !== "ready") {
      void vscode.window.showWarningMessage(
        "Connect to the Repowise server before scoring branch risk.",
      );
      return;
    }
    const repoId = ctx.repoId;
    if (!repoId) {
      void vscode.window.showWarningMessage(
        "This repository is not indexed by the Repowise server.",
      );
      return;
    }

    // Base resolution: explicit setting, else the server's default branch,
    // else "main". An empty setting means "auto".
    const configured = vscode.workspace
      .getConfiguration(CONFIG_SECTION)
      .get<string>("risk.baseBranch", "")
      .trim();
    const base = configured || ctx.repo?.default_branch || "main";

    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `Scoring branch risk against ${base}`,
      },
      async (): Promise<RiskRangeResponse | null> => {
        try {
          return await getRiskRange(repoId, { base, head: "HEAD" });
        } catch (err) {
          if (err instanceof ApiClientError) {
            // The server rev-parses inputs and returns 400 on an unknown rev.
            void vscode.window.showWarningMessage(
              `Could not score branch risk: ${err.detail}`,
            );
            return null;
          }
          ctx.log.error(`branch risk failed: ${String(err)}`);
          void vscode.window.showWarningMessage(
            "Could not score branch risk. See the Repowise log for details.",
          );
          return null;
        }
      },
    );
    if (!result) return;

    const branch = await getCurrentBranchName(ctx.workspace.repoRoot ?? "");
    content = renderReport(result, base, branch);
    changeEmitter.fire(RISK_URI);
    // Ensure the preview re-reads the updated content on a repeat invocation.
    await vscode.workspace.openTextDocument(RISK_URI);
    await vscode.commands.executeCommand("markdown.showPreview", RISK_URI);
  }

  const disposables: vscode.Disposable[] = [
    vscode.workspace.registerTextDocumentContentProvider(RISK_SCHEME, provider),
    vscode.commands.registerCommand(Commands.checkBranchRisk, () => void run()),
    changeEmitter,
  ];

  return {
    dispose(): void {
      for (const d of disposables) d.dispose();
    },
  };
}

/** Formats a contribution with an explicit sign (positive raises risk). */
function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

/** Builds the markdown body for a scored range. */
function renderReport(
  r: RiskRangeResponse,
  base: string,
  branch: string | null,
): string {
  const lines: string[] = [];
  const head = branch ?? "HEAD";

  lines.push(`# Branch risk: ${r.score.toFixed(1)}/10 (${r.level})`);
  lines.push("");
  lines.push(`\`${head}\` vs \`${base}\``);
  lines.push("");
  lines.push(`- Defect probability: ${(r.probability * 100).toFixed(1)}%`);
  if (r.risk_percentile !== null) {
    // Served as 0-100 already, not a fraction.
    lines.push(`- Risk percentile: ${r.risk_percentile.toFixed(0)}%`);
  }
  if (r.review_priority !== null) {
    lines.push(`- Review priority: ${r.review_priority}`);
  }
  if (r.is_fix) {
    lines.push("- Classified as a fix change.");
  }

  if (r.drivers.length > 0) {
    lines.push("");
    lines.push("## Drivers");
    lines.push("");
    const ordered = [...r.drivers].sort(
      (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
    );
    for (const d of ordered) {
      lines.push(`- ${d.label}: ${signed(d.contribution)}`);
    }
  }

  // A feature can be null (author experience on shallow history); skip those.
  const featureRows = FEATURE_LABELS.filter(
    ([key]) => key in r.features && r.features[key] != null,
  );
  if (featureRows.length > 0) {
    lines.push("");
    lines.push("## Features");
    lines.push("");
    lines.push("| Feature | Value |");
    lines.push("| --- | --- |");
    for (const [key, label] of featureRows) {
      lines.push(`| ${label} | ${r.features[key]} |`);
    }
  }

  lines.push("");
  return lines.join("\n");
}

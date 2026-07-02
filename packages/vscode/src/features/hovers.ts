import * as vscode from "vscode";
import { CONFIG_SECTION, Commands } from "../constants";
import { getHealthFileBreakdown } from "@repowise-dev/api-client/code-health";
import { listDecisions } from "@repowise-dev/api-client/decisions";
import { listSymbols } from "@repowise-dev/api-client/symbols";
import type { RepowiseContext } from "../core/context";
import { getFileFindings, repoRelativePath } from "../core/fileSignals";
import type {
  HealthFileBreakdownResponse,
  HealthFinding,
} from "@repowise-dev/types/health";
import type { DecisionRecordResponse } from "@repowise-dev/api-client/types";
import type { SymbolResponse } from "@repowise-dev/api-client/types";

/** Max governing decisions surfaced on a file hover. */
const MAX_DECISIONS = 3;

/** Renders a score, or a dash when the dimension is absent from the payload. */
function fmt(value: number | null | undefined): string {
  return value == null ? "-" : value.toFixed(1);
}

/** True when a finding's 1-based line span covers the given 0-based line. */
function covers(finding: HealthFinding, line: number): boolean {
  if (finding.line_start == null) return false;
  const start = finding.line_start - 1;
  const end = (finding.line_end ?? finding.line_start) - 1;
  return line >= start && line <= end;
}

/**
 * Provides health context on hover. Hovering line 0 of a file with health data
 * shows the three scores, its primary owner, and the decisions that govern it;
 * hovering a symbol body shows the symbol name and a link to a matching finding.
 * Every other position returns nothing, so hovering stays cheap.
 */
export function registerHovers(ctx: RepowiseContext): vscode.Disposable {
  const enabled = (): boolean =>
    vscode.workspace
      .getConfiguration(CONFIG_SECTION)
      .get<boolean>("hover.enabled", true);

  /** Reads through the shared cache under the current head commit. */
  async function cached<T>(
    key: string,
    fetcher: () => Promise<T>,
    fallback: T,
  ): Promise<T> {
    const repoId = ctx.repoId;
    if (!repoId) return fallback;
    const tag = ctx.repo?.head_commit ?? "";
    const hit = ctx.cache.get<T>(repoId, key, tag);
    if (hit !== undefined) return hit;
    try {
      const value = await fetcher();
      ctx.cache.set(repoId, key, tag, value);
      return value;
    } catch (err) {
      ctx.log.debug(`hover fetch ${key} failed: ${String(err)}`);
      return fallback;
    }
  }

  async function fileHover(
    relPath: string,
  ): Promise<vscode.Hover | undefined> {
    const repoId = ctx.repoId;
    if (!repoId) return undefined;

    const breakdown = await cached<HealthFileBreakdownResponse | null>(
      `breakdown:${relPath}`,
      () => getHealthFileBreakdown(repoId, relPath),
      null,
    );
    const metric = breakdown?.metric;
    if (!metric) return undefined;

    const decisions = await cached<DecisionRecordResponse[]>(
      "decisions:all",
      () => listDecisions(repoId, { limit: 500 }),
      [],
    );
    const governing = decisions
      .filter((d) => d.affected_files.includes(relPath))
      .slice(0, MAX_DECISIONS);

    const md = new vscode.MarkdownString();
    md.appendMarkdown("**Repowise health**\n\n");
    md.appendMarkdown(
      `Defect ${fmt(metric.defect_score ?? metric.score)} · Maintainability ${fmt(
        metric.maintainability_score,
      )} · Performance ${fmt(metric.performance_score)}\n\n`,
    );

    const owner = breakdown?.signals?.primary_owner_name;
    if (owner) {
      const pct = breakdown?.signals?.primary_owner_commit_pct;
      md.appendMarkdown(
        `Owner: ${owner}${pct == null ? "" : ` (${Math.round(pct)}%)`}\n\n`,
      );
    }

    if (governing.length > 0) {
      md.appendMarkdown("Decisions:\n\n");
      for (const decision of governing) {
        md.appendMarkdown(`- ${decision.title}\n`);
      }
    }
    return new vscode.Hover(md);
  }

  async function symbolHover(
    relPath: string,
    position: vscode.Position,
  ): Promise<vscode.Hover | undefined> {
    const repoId = ctx.repoId;
    if (!repoId) return undefined;

    const symbols = await cached<SymbolResponse[]>(
      `symbols:${relPath}`,
      () => listSymbols({ repo_id: repoId, file_path: relPath, limit: 1000 }),
      [],
    );
    const line1 = position.line + 1;
    // Innermost symbol covering the line: smallest span among those that match.
    let match: SymbolResponse | undefined;
    for (const symbol of symbols) {
      if (symbol.start_line <= line1 && line1 <= symbol.end_line) {
        if (!match || symbol.end_line - symbol.start_line < match.end_line - match.start_line) {
          match = symbol;
        }
      }
    }
    if (!match) return undefined;

    const md = new vscode.MarkdownString();
    md.isTrusted = true;
    md.appendMarkdown(`**${match.name}**\n\n`);

    const findings = await getFileFindings(ctx, relPath);
    const finding = findings.find((f) => covers(f, position.line));
    if (finding) {
      md.appendMarkdown(
        `[${finding.reason}](command:${Commands.showFileHealth})\n`,
      );
    }
    return new vscode.Hover(md);
  }

  const provider = vscode.languages.registerHoverProvider(
    { scheme: "file" },
    {
      provideHover: async (document, position) => {
        if (!enabled() || ctx.getExtensionState() !== "ready" || !ctx.repoId) {
          return undefined;
        }
        const rel = repoRelativePath(ctx, document.uri);
        if (!rel) return undefined;
        return position.line === 0
          ? fileHover(rel)
          : symbolHover(rel, position);
      },
    },
  );

  return provider;
}

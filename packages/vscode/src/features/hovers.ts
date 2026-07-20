import * as vscode from "vscode";
import { CONFIG_SECTION, Commands } from "../constants";
import { getHealthFileBreakdown } from "@repowise-dev/api-client/code-health";
import { listDecisions } from "@repowise-dev/api-client/decisions";
import { getSymbolDetail, listSymbols } from "@repowise-dev/api-client/symbols";
import type { RepowiseContext } from "../core/context";
import { getFileFindings, repoRelativePath } from "../core/fileSignals";
import { formatRelativeTimeOrNull } from "@repowise-dev/ui/lib/format";
import type {
  FileSignals,
  HealthFileBreakdownResponse,
  HealthFinding,
} from "@repowise-dev/types/health";
import type { DecisionRecordResponse } from "@repowise-dev/api-client/types";
import type { SymbolResponse } from "@repowise-dev/api-client/types";
import type { SymbolDetailResponse } from "@repowise-dev/types/symbols";

/** Max governing decisions surfaced on a file hover. */
const MAX_DECISIONS = 3;

/** Max governing decisions surfaced on a symbol hover. */
const MAX_SYMBOL_DECISIONS = 2;

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
 * hovering a symbol body shows the symbol name, optionally a compact detail
 * card (kind, caller/callee counts, owner, governing decisions), and a link to
 * a matching finding. Every other position returns nothing, so hovering stays
 * cheap.
 */
export function registerHovers(ctx: RepowiseContext): vscode.Disposable {
  const enabled = (): boolean =>
    vscode.workspace
      .getConfiguration(CONFIG_SECTION)
      .get<boolean>("hover.enabled", true);

  const symbolDetailEnabled = (): boolean =>
    vscode.workspace
      .getConfiguration(CONFIG_SECTION)
      .get<boolean>("hover.symbolDetail", true);

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

  /**
   * One line of counted bug-fix history, or nothing.
   *
   * Recency is not optional in this copy: `bug_magnet` is a claim about recent
   * fix pressure, so it is dropped rather than shown without the age beside it.
   * Silent when the file has no counted fixes, so a clean file's hover is
   * unchanged. Aggregate only, and no commit is named here or anywhere else.
   */
  function fixHistoryLine(signals: FileSignals | null | undefined): string | null {
    const count = signals?.prior_defect_count;
    if (count == null || count <= 0) return null;
    const last = formatRelativeTimeOrNull(signals?.last_fix_at ?? null, "");
    if (!last) return `Bug fixes: ${count} in 6 months`;
    const magnet = signals?.bug_magnet ? " **bug magnet**" : "";
    return `Bug fixes: ${count} in 6 months, last ${last}${magnet}`;
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

    const bugHistory = fixHistoryLine(breakdown?.signals);
    if (bugHistory) {
      md.appendMarkdown(`${bugHistory}\n\n`);
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
    token: vscode.CancellationToken,
  ): Promise<vscode.Hover | undefined> {
    const repoId = ctx.repoId;
    if (!repoId) return undefined;

    const symbols = await cached<SymbolResponse[]>(
      `symbols:${relPath}`,
      () => listSymbols({ repo_id: repoId, file_path: relPath, limit: 1000 }),
      [],
    );
    if (token.isCancellationRequested) return undefined;
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
    const symbol = match;

    // One lazy detail fetch per hover, cached per symbol under the head
    // commit. Failures cache as null so a dead symbol id is asked once per
    // index version, and the hover degrades to the minimal name-only card.
    const detailPromise: Promise<SymbolDetailResponse | null> =
      symbolDetailEnabled()
        ? cached<SymbolDetailResponse | null>(
            `symbolDetail:${symbol.symbol_id}`,
            async () => {
              try {
                return await getSymbolDetail(repoId, symbol.symbol_id);
              } catch (err) {
                ctx.log.debug(`symbol detail ${symbol.symbol_id}: ${String(err)}`);
                return null;
              }
            },
            null,
          )
        : Promise.resolve(null);
    const [detail, findings] = await Promise.all([
      detailPromise,
      getFileFindings(ctx, relPath),
    ]);
    if (token.isCancellationRequested) return undefined;

    const md = new vscode.MarkdownString();
    // Trusted only for our own command link below; index-derived strings
    // (titles, owners) must not be able to smuggle arbitrary command URIs.
    md.isTrusted = { enabledCommands: [Commands.showFileHealth] };
    if (detail) {
      md.appendMarkdown(`**${match.name}** · ${detail.symbol.kind}\n\n`);
      const callers = detail.graph.in_degree;
      const callees = detail.graph.out_degree;
      md.appendMarkdown(`${callers} callers · ${callees} callees\n\n`);
      const owner = detail.file_context?.primary_owner;
      if (owner) {
        md.appendMarkdown(`Owner: ${owner}\n\n`);
      }
      const governing = detail.governing_decisions.slice(0, MAX_SYMBOL_DECISIONS);
      if (governing.length > 0) {
        md.appendMarkdown("Decisions:\n\n");
        for (const decision of governing) {
          md.appendMarkdown(`- ${decision.title}\n`);
        }
        md.appendMarkdown("\n");
      }
    } else {
      md.appendMarkdown(`**${match.name}**\n\n`);
    }

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
      provideHover: async (document, position, token) => {
        if (!enabled() || ctx.getExtensionState() !== "ready" || !ctx.repoId) {
          return undefined;
        }
        const rel = repoRelativePath(ctx, document.uri);
        if (!rel) return undefined;
        return position.line === 0
          ? fileHover(rel)
          : symbolHover(rel, position, token);
      },
    },
  );

  return provider;
}

import * as vscode from "vscode";
import { getHealthFileBreakdown } from "@repowise-dev/api-client/code-health";
import { getFileDetail } from "@repowise-dev/api-client/files";
import { getRiskRange } from "@repowise-dev/api-client/risk";
import { search } from "@repowise-dev/api-client/search";
import { getSymbolDetail, listSymbols } from "@repowise-dev/api-client/symbols";
import type { SymbolResponse } from "@repowise-dev/api-client/types";
import type { HealthFileBreakdownResponse } from "@repowise-dev/types/health";
import type { SymbolDetailResponse } from "@repowise-dev/types/symbols";
import { AGENT_TOOLS_CONTEXT_KEY, CONFIG_SECTION, LmToolNames } from "../constants";
import type { RepowiseContext } from "../core/context";
import { getFileFindings } from "../core/fileSignals";
import { getPlansForFile } from "../core/plans";

/**
 * Native language model tools over the Repowise index, mirroring the
 * `languageModelTools` contribution in package.json. All six tools are
 * read-only lookups against the local server; chat invokes them, so every
 * outcome (including "disabled" and "not connected") flows back as a tool
 * result rather than a toast.
 */

/** Hard ceiling on any tool result; the model gets a truncation note, not an error. */
const MAX_RESULT_CHARS = 30000;

/** Caps mirroring the manifest contract: bounded payloads, never firehoses. */
const MAX_SEARCH_RESULTS = 20;
const DEFAULT_SEARCH_RESULTS = 5;
const MAX_FINDINGS = 50;
const MAX_PLANS = 10;
const MAX_SYMBOLS = 100;
const MAX_SYMBOL_CANDIDATES = 5;

/** Input shapes mirrored from the package.json inputSchema declarations. */
interface SearchInput {
  query: string;
  limit?: number;
}
interface FileInput {
  filePath: string;
}
interface BranchRiskInput {
  base?: string;
}
interface SymbolInput {
  filePath: string;
  symbolName?: string;
}

function textResult(text: string): vscode.LanguageModelToolResult {
  const bounded =
    text.length > MAX_RESULT_CHARS
      ? `${text.slice(0, MAX_RESULT_CHARS)}\n[truncated]`
      : text;
  return new vscode.LanguageModelToolResult([
    new vscode.LanguageModelTextPart(bounded),
  ]);
}

function jsonResult(payload: unknown): vscode.LanguageModelToolResult {
  return textResult(JSON.stringify(payload));
}

/**
 * Normalizes a repo-relative path from the model: forward slashes, no leading
 * "./". Returns null for absolute paths or ".." segments; the caller answers
 * with a polite refusal instead of letting the path reach the server.
 */
function normalizeRelPath(input: string): string | null {
  let rel = input.replace(/\\/g, "/").trim();
  if (rel.startsWith("./")) rel = rel.slice(2);
  if (!rel) return null;
  if (rel.startsWith("/") || /^[A-Za-z]:/.test(rel)) return null;
  if (rel.split("/").some((segment) => segment === "..")) return null;
  return rel;
}

function agentToolsEnabled(): boolean {
  return vscode.workspace
    .getConfiguration(CONFIG_SECTION)
    .get<boolean>("agentTools.enabled", true);
}

export function registerLmTools(ctx: RepowiseContext): vscode.Disposable {
  // The manifest `when` clauses hide the tools from pickers via this context
  // key; the per-invoke guard below is the backstop for calls already in
  // flight or hosts that ignore the clause.
  const syncContextKey = (): void => {
    void vscode.commands.executeCommand(
      "setContext",
      AGENT_TOOLS_CONTEXT_KEY,
      agentToolsEnabled(),
    );
  };
  syncContextKey();
  const configListener = vscode.workspace.onDidChangeConfiguration((event) => {
    if (event.affectsConfiguration(`${CONFIG_SECTION}.agentTools.enabled`)) {
      syncContextKey();
    }
  });

  /**
   * Reads through the shared cache under the current head commit, mirroring
   * plans.ts. Fetch failures propagate to the per-tool catch, which turns
   * them into a text result.
   */
  async function cached<T>(key: string, fetcher: (repoId: string) => Promise<T>): Promise<T> {
    const repoId = ctx.repoId;
    if (!repoId) throw new Error("No repository resolved.");
    const tag = ctx.repo?.head_commit ?? "";
    const hit = ctx.cache.get<T>(repoId, key, tag);
    if (hit !== undefined) return hit;
    const value = await fetcher(repoId);
    ctx.cache.set(repoId, key, tag, value);
    return value;
  }

  /**
   * Wraps a tool body with the shared guards: setting toggle, server
   * readiness, and a catch that reports failures as tool text. Tools never
   * throw for expected states and never surface UI notifications.
   */
  function makeTool<T>(
    invocationMessage: string,
    run: (input: T, token: vscode.CancellationToken) => Promise<vscode.LanguageModelToolResult>,
  ): vscode.LanguageModelTool<T> {
    return {
      prepareInvocation: () => ({ invocationMessage }),
      invoke: async (options, token) => {
        if (!agentToolsEnabled()) {
          return textResult(
            "The Repowise tools are disabled in settings (repowise.agentTools.enabled).",
          );
        }
        if (ctx.getExtensionState() !== "ready" || !ctx.repoId) {
          return textResult(
            "The local Repowise server is not connected. The user can start it from the Repowise status bar or the \"Repowise: Start Server\" command.",
          );
        }
        try {
          return await run(options.input, token);
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          ctx.log.error(`Language model tool failed: ${String(err)}`);
          return textResult(`Repowise lookup failed: ${message}`);
        }
      },
    };
  }

  /** Path-taking tools share the normalization refusal. */
  function badPathResult(raw: string): vscode.LanguageModelToolResult {
    return textResult(
      `The path "${raw}" is not a valid repository-relative path. Use forward slashes, relative to the repository root, without ".." segments.`,
    );
  }

  const searchTool = makeTool<SearchInput>(
    "Searching the Repowise index",
    async (input) => {
      const repoId = ctx.repoId ?? "";
      const raw = typeof input.limit === "number" ? Math.floor(input.limit) : DEFAULT_SEARCH_RESULTS;
      const limit = Math.min(Math.max(raw, 1), MAX_SEARCH_RESULTS);
      // Queries form an unbounded space, so results are never cached.
      const results = await search(input.query, { limit, repo_id: repoId });
      const payload = results.map((r) => ({
        title: r.title,
        page_type: r.page_type,
        target_path: r.target_path,
        score: r.score,
        snippet: r.snippet,
      }));
      return jsonResult(payload);
    },
  );

  const fileHealthTool = makeTool<FileInput>(
    "Reading file health from Repowise",
    async (input) => {
      const rel = normalizeRelPath(input.filePath);
      if (!rel) return badPathResult(input.filePath);
      // Same cache key the hover uses, so a hovered file costs the tool nothing.
      const breakdown = await cached<HealthFileBreakdownResponse>(
        `breakdown:${rel}`,
        (repoId) => getHealthFileBreakdown(repoId, rel),
      );
      const metric = breakdown.metric;
      if (!metric) {
        return textResult(`Repowise has no health data for ${rel}.`);
      }
      const findings = await getFileFindings(ctx, rel);
      return jsonResult({
        scores: {
          defect: metric.defect_score ?? metric.score,
          maintainability: metric.maintainability_score ?? null,
          performance: metric.performance_score ?? null,
        },
        findings: findings.slice(0, MAX_FINDINGS).map((f) => ({
          severity: f.severity,
          dimension: f.dimension ?? "defect",
          biomarker: f.biomarker_type,
          line_start: f.line_start,
          line_end: f.line_end,
          message: f.reason,
        })),
      });
    },
  );

  const plansTool = makeTool<FileInput>(
    "Fetching Repowise refactoring plans",
    async (input) => {
      const rel = normalizeRelPath(input.filePath);
      if (!rel) return badPathResult(input.filePath);
      const plans = await getPlansForFile(ctx, rel);
      if (plans.length === 0) {
        return textResult(`Repowise has no refactoring plans for ${rel}.`);
      }
      const payload = plans.slice(0, MAX_PLANS).map((p) => ({
        id: p.id,
        refactoring_type: p.refactoring_type,
        target_symbol: p.target_symbol,
        line_start: p.line_start,
        line_end: p.line_end,
        impact_delta: p.impact_delta,
        effort_bucket: p.effort_bucket,
        confidence: p.confidence,
        plan: p.plan,
        evidence: p.evidence,
        blast_radius: p.blast_radius,
      }));
      return jsonResult(payload);
    },
  );

  const branchRiskTool = makeTool<BranchRiskInput>(
    "Scoring branch risk with Repowise",
    async (input) => {
      const repoId = ctx.repoId ?? "";
      const configured = vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<string>("risk.baseBranch", "")
        .trim();
      const base =
        input.base?.trim() || configured || ctx.repo?.default_branch || "main";
      // Scores the working HEAD, which moves independently of the index, so
      // this always fetches fresh.
      const result = await getRiskRange(repoId, { base, head: "HEAD" });
      return jsonResult({
        base,
        score: result.score,
        level: result.level,
        probability: result.probability,
        risk_percentile: result.risk_percentile,
        review_priority: result.review_priority,
        drivers: result.drivers,
        features: result.features,
      });
    },
  );

  const symbolInfoTool = makeTool<SymbolInput>(
    "Looking up symbol info in Repowise",
    async (input) => {
      const rel = normalizeRelPath(input.filePath);
      if (!rel) return badPathResult(input.filePath);
      // Same cache key the hover uses, so both features share one fetch.
      const symbols = await cached<SymbolResponse[]>(`symbols:${rel}`, (repoId) =>
        listSymbols({ repo_id: repoId, file_path: rel, limit: 1000 }),
      );
      if (symbols.length === 0) {
        return textResult(`Repowise has no indexed symbols for ${rel}.`);
      }

      const name = input.symbolName?.trim();
      if (!name) {
        const payload = symbols.slice(0, MAX_SYMBOLS).map((s) => ({
          name: s.name,
          kind: s.kind,
          line_start: s.start_line,
          line_end: s.end_line,
        }));
        return jsonResult(payload);
      }

      const match =
        symbols.find((s) => s.name === name) ??
        symbols.find((s) => s.name.toLowerCase() === name.toLowerCase());
      if (!match) {
        const lower = name.toLowerCase();
        const near = symbols.filter((s) => s.name.toLowerCase().includes(lower));
        const candidates = (near.length > 0 ? near : symbols)
          .slice(0, MAX_SYMBOL_CANDIDATES)
          .map((s) => s.name);
        return textResult(
          `No symbol named "${name}" in ${rel}. Close candidates: ${candidates.join(", ")}.`,
        );
      }

      // Shares the hover's cache key and its convention: a failed fetch caches
      // as null so a dead symbol id is asked once per index version.
      const detail = await cached<SymbolDetailResponse | null>(
        `symbolDetail:${match.symbol_id}`,
        async (repoId) => {
          try {
            return await getSymbolDetail(repoId, match.symbol_id);
          } catch (err) {
            ctx.log.debug(`symbol detail ${match.symbol_id}: ${String(err)}`);
            return null;
          }
        },
      );
      if (!detail) {
        return jsonResult({
          name: match.name,
          kind: match.kind,
          lines: { start: match.start_line, end: match.end_line },
          note: "Detail lookup unavailable for this symbol.",
        });
      }
      return jsonResult({
        name: match.name,
        kind: detail.symbol.kind,
        lines: { start: match.start_line, end: match.end_line },
        owner: detail.file_context?.primary_owner ?? null,
        callers: detail.graph.callers.map((c) => ({
          name: c.name,
          kind: c.kind,
          file: c.file,
          line: c.start_line,
        })),
        callees: detail.graph.callees.map((c) => ({
          name: c.name,
          kind: c.kind,
          file: c.file,
          line: c.start_line,
        })),
        decisions: detail.governing_decisions.map((d) => d.title),
      });
    },
  );

  const fileDocsTool = makeTool<FileInput>(
    "Reading Repowise docs",
    async (input) => {
      const rel = normalizeRelPath(input.filePath);
      if (!rel) return badPathResult(input.filePath);
      // The file-detail aggregate carries the wiki page content inline, the
      // same fetch the docs panel resolves a file through.
      const detail = await cached(`lm:docs:${rel}`, (repoId) =>
        getFileDetail(repoId, rel),
      );
      const page = detail.wiki_page;
      if (!page) {
        return textResult(`Repowise has no documentation page for ${rel}.`);
      }
      const header = JSON.stringify({ title: page.title, path: rel });
      return textResult(`${header}\n\n# ${page.title}\n\n${page.content}`);
    },
  );

  const registrations: vscode.Disposable[] = [configListener];
  // Fail soft per tool: a host that rejects one registration (an older
  // proposal surface, a name collision) should not take down the rest.
  function safeRegister<T>(toolName: string, tool: vscode.LanguageModelTool<T>): void {
    try {
      registrations.push(vscode.lm.registerTool(toolName, tool));
    } catch (err) {
      ctx.log.warn(`Could not register language model tool ${toolName}: ${String(err)}`);
    }
  }
  safeRegister(LmToolNames.searchCodebase, searchTool);
  safeRegister(LmToolNames.getFileHealth, fileHealthTool);
  safeRegister(LmToolNames.getRefactoringPlans, plansTool);
  safeRegister(LmToolNames.getBranchRisk, branchRiskTool);
  safeRegister(LmToolNames.getSymbolInfo, symbolInfoTool);
  safeRegister(LmToolNames.getFileDocs, fileDocsTool);
  return vscode.Disposable.from(...registrations);
}

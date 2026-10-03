/**
 * Every string Lens shows. Commands are written out whole so they can be
 * copied; Lens never runs them.
 */

import { countOf } from "../format";
import type { FileContext, HintKind, IndexFreshness, MarginNote, SavingsDelta } from "../model/session";
import type { Squeeze } from "../model/squeeze";

export const HINTS: Record<HintKind, string> = {
  "no-server": "Lens map needs the local server: repowise serve --no-ui",
  auth: "local server needs an API key; Lens does not read keys",
  unlisted: "Lens map needs a local server for this repo: repowise serve --no-ui",
  "no-index": "index this repo for Lens: repowise init --no-prose --yes",
  "no-cli": "Lens needs the Repowise CLI: pip install repowise",
};

export function freshnessLine(f: IndexFreshness): string {
  const changed = f.changedFiles === null ? "" : ` (${countOf(f.changedFiles, "file", "files")} changed)`;
  return `index behind HEAD${changed} · repowise update`;
}

/** `query.py · 41 caller files · 3 contributors`; unknown or zero counts are left out. */
export function spinnerLine(file: string, ctx: FileContext): string {
  const parts = [file.slice(file.lastIndexOf("/") + 1)];
  if (ctx.callerFiles) parts.push(countOf(ctx.callerFiles, "caller file", "caller files"));
  if (ctx.contributors) parts.push(countOf(ctx.contributors, "contributor", "contributors"));
  return parts.join(" · ");
}

/** `1,204 lines → 38 · ~9,400 tokens omitted · 4 failed · repowise expand 9c1e4b2a7f01`. */
export function squeezeLine(s: Squeeze): string {
  const parts = [`${countOf(s.originalLines, "line", "lines")} → ${s.keptLines.toLocaleString("en-US")}`];
  if (s.omittedTokens > 0) parts.push(`~${countOf(s.omittedTokens, "token", "tokens")} omitted`);
  // As the runner wrote it: pytest's `4 failed, 1 error`.
  const failures = [s.failed > 0 ? `${s.failed.toLocaleString("en-US")} failed` : "", s.errors > 0 ? countOf(s.errors, "error", "errors") : ""];
  if (s.failed + s.errors > 0) parts.push(failures.filter((f) => f !== "").join(", "));
  const more = s.refs.length > 1 ? ` (+${s.refs.length - 1} more)` : "";
  parts.push(`repowise expand ${s.refs[0]}${more}`);
  return parts.join(" · ");
}

/**
 * A margin note as help, never a label, saying only what the hook emitted.
 * The age is always said, so a run of fixes last week never reads like one
 * from last spring.
 */
export function marginLine(note: MarginNote): string {
  if (note.kind === "decision") {
    return note.reviewed
      ? `a standing decision covers this file: ${note.title}`
      : `a decision found in this file, not yet reviewed: ${note.title}`;
  }
  const where = note.symbol === null ? "" : `, mostly in ${note.symbol}`;
  return `fixed ${countOf(note.count, "time", "times")} in 6 months, most recently ${note.age}${where}`;
}

/**
 * `182,400 tokens · $2.41 saved since this session started · all agents on this repo`.
 * The scope stays on screen: a narrow band gets the short form, not a cut one.
 */
export function savingsLine(d: SavingsDelta, columns: number): string {
  const inferred = d.inferredTokens > 0 ? ` (${d.inferredTokens.toLocaleString("en-US")} inferred)` : "";
  // Input-side dollars, like the token count. Under half a cent rounds to
  // $0.00, which would read as nothing priced.
  const usd = d.usd >= 0.005 ? ` · $${d.usd.toFixed(2)}` : "";
  const head = `${countOf(d.tokens, "token", "tokens")}${inferred}${usd} saved`;
  const full = `${head} since this session started · all agents on this repo`;
  return full.length <= columns ? full : `${head} this session · all agents`;
}

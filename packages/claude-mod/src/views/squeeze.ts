/**
 * The squeeze row under a Bash result that `repowise distill` shortened: a
 * bar for the share kept, then the row's words. Parsing lives in
 * model/squeeze.ts.
 */

import type { Squeeze } from "../model/squeeze";
import { squeezeLine } from "./copy";
import { text, type Node } from "./elements";

export const BAR_CELLS = 12;

/** Filled cells for what was kept, at least one so a deep cut still shows a mark. */
export function squeezeBar(s: Squeeze, cells = BAR_CELLS): string {
  const filled = Math.min(cells, Math.max(1, Math.round((cells * s.keptLines) / Math.max(1, s.originalLines))));
  return `${"█".repeat(filled)}${"░".repeat(cells - filled)}`;
}

export function squeezeView(s: Squeeze): Node {
  return text(`${squeezeBar(s)} ${squeezeLine(s)}`, { dimColor: true, wrap: "truncate-end" });
}

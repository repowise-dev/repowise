/**
 * Every string Lens shows. Commands are written out whole so they can be
 * copied; Lens never runs them.
 */

import { countOf } from "../format";
import type { HintKind, IndexFreshness } from "../model/session";

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

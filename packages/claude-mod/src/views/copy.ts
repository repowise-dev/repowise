/**
 * Every string Lens shows. Commands are written out whole so they can be
 * copied; Lens never runs them.
 */

import { countOf } from "../format";
import type { HintKind, IndexFreshness } from "../model/session";

export const HINTS: Record<HintKind, string> = {
  "no-server": "Map needs the local server: repowise serve --no-ui",
  auth: "Map is off: the local server requires an API key, and Lens never reads keys",
  unlisted: "Map needs a local server for this repo: repowise serve --no-ui",
  "no-index": "Index this repo for Lens: repowise init --no-prose -y",
  "no-cli": "Lens needs the Repowise CLI: pip install repowise",
};

export function freshnessLine(f: IndexFreshness): string {
  const behind = f.changedFiles === null ? "index behind HEAD" : `index ${countOf(f.changedFiles, "file", "files")} behind HEAD`;
  return `${behind} · repowise update`;
}

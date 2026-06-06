// Reader personas — a client-side lens that filters which sections of a
// generated page render, so the same rich page serves three audiences without
// regenerating anything:
//
//   overview     — non-technical / first look: prose + diagrams only. Hides
//                  symbol dumps, call graphs, dependency lists, raw metrics.
//   contributor  — the default: everything except the rawest reference dumps.
//   deep         — everything, including call graphs, metrics, dead code.
//
// Filtering is done by splitting the markdown on `## ` (H2) section headings
// and dropping sections whose heading matches the persona's hide-list. Content
// before the first H2 (the title + lead paragraph) is always kept.

export type ReaderPersona = "overview" | "contributor" | "deep";

export const READER_PERSONAS: { value: ReaderPersona; label: string; hint: string }[] = [
  { value: "overview", label: "Overview", hint: "Prose & diagrams only" },
  { value: "contributor", label: "Contributor", hint: "Balanced (default)" },
  { value: "deep", label: "Deep", hint: "Everything" },
];

export const DEFAULT_PERSONA: ReaderPersona = "contributor";

export function isReaderPersona(value: string | null | undefined): value is ReaderPersona {
  return value === "overview" || value === "contributor" || value === "deep";
}

// Heading keywords (lowercased, matched as a prefix of the heading text) that
// each persona hides. Matching is intentionally generous so it works across
// the file/module/layer page templates without an exhaustive list.
const OVERVIEW_HIDE = [
  "symbols",
  "symbol",
  "public api",
  "imports",
  "exports",
  "dependencies",
  "dependents",
  "call graph",
  "class hierarchy",
  "community",
  "neighbors",
  "raw metrics",
  "dead code",
  "parse errors",
  "source",
  "source snippet",
  "metrics",
];

// Contributor keeps almost everything; only the rawest machine-oriented dumps
// are hidden so the page stays readable.
const CONTRIBUTOR_HIDE = ["raw metrics", "parse errors", "source snippet"];

const HIDE_BY_PERSONA: Record<ReaderPersona, string[]> = {
  overview: OVERVIEW_HIDE,
  contributor: CONTRIBUTOR_HIDE,
  deep: [],
};

function headingHidden(headingText: string, hideList: string[]): boolean {
  const h = headingText.trim().toLowerCase();
  return hideList.some((kw) => h === kw || h.startsWith(kw));
}

/**
 * Filter a markdown document to the sections visible for *persona*.
 *
 * Splits on top-level (`## `) headings. The preamble before the first H2 is
 * always retained. Returns the original content unchanged for the `deep`
 * persona (or when there is nothing to hide).
 */
/**
 * Whether persona filtering changes anything for this content — i.e. at least
 * one H2 matches a hide-list. Curated pages (onboarding, overviews, diagrams)
 * have none, so the reader-level control would be a no-op; callers use this
 * to hide it. `deep` never filters, so only the two filtering personas count.
 */
export function personaFilteringApplies(content: string): boolean {
  const base = content.trim();
  return (
    filterMarkdownByPersona(content, "overview") !== base ||
    filterMarkdownByPersona(content, "contributor") !== base
  );
}

export function filterMarkdownByPersona(content: string, persona: ReaderPersona): string {
  const hideList = HIDE_BY_PERSONA[persona];
  if (!hideList || hideList.length === 0) return content;

  const lines = content.split("\n");
  const out: string[] = [];
  let hiding = false;
  let inFence = false;

  for (const line of lines) {
    // Track fenced code blocks so a `##` inside code isn't treated as a heading.
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      if (!hiding) out.push(line);
      continue;
    }
    const h2 = !inFence ? /^##\s+(.+?)\s*$/.exec(line) : null;
    if (h2) {
      hiding = headingHidden(h2[1] ?? "", hideList);
      if (!hiding) out.push(line);
      continue;
    }
    if (!hiding) out.push(line);
  }

  return out.join("\n").trim();
}

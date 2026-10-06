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

// Whole section headings (lowercased) that each persona hides. Exact matches
// only: a model-written page names its sections after what the code does, and
// a prefix such as "source" or "symbol" would delete a section called
// "Source files become symbols". Each entry is a heading a structural template
// emits (see ``structural_labels.py`` and the stub templates).

// Contributor hides only retrieval scaffolding a file or symbol page still
// carries in `content`: the question block, written so the index has the words
// a question is asked in, and the importer list a symbol page keeps once its
// resolved callers have answered the question. Pages with an agent digest
// carry that material outside `content`, and the reader offers it as the
// Reference view instead.
const CONTRIBUTOR_HIDE = ["questions this page answers", "files importing this module"];

// Overview is contributor plus the reference dumps. Spread, not restated: a
// section the balanced lens hides must never reappear in the narrower one.
const OVERVIEW_HIDE = [
  ...CONTRIBUTOR_HIDE,
  "public api",
  "source",
  "symbols defined in the cycle",
  "dependencies (modules this imports)",
  "dependents (modules that import this)",
];

const HIDE_BY_PERSONA: Record<ReaderPersona, string[]> = {
  overview: OVERVIEW_HIDE,
  contributor: CONTRIBUTOR_HIDE,
  deep: [],
};

function headingHidden(headingText: string, hideList: string[]): boolean {
  return hideList.includes(headingText.trim().toLowerCase());
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

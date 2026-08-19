// Present mode — the data model for an on-the-fly slide deck + guided
// walkthrough, built entirely from already-generated wiki pages. No new
// generation, no network, no DB: `buildPresentModel` (build-present-model.ts)
// transforms the loaded `DocPage[]` into this shape, and the overlay renders it.
//
// Kept framework-free (plain data) so the same model serves the OSS dashboard
// and a future hosted app without re-implementing any logic.

// "split" pairs overview prose with a diagram side by side (used for layer
// pages that carry a deterministic architecture diagram).
export type PresentSlideKind = "title" | "section" | "diagram" | "split" | "closing";

export interface PresentSlide {
  /** Stable id within the deck (slug), used as a React key. */
  id: string;
  kind: PresentSlideKind;
  /** Small uppercase label above the title (e.g. "Architecture", "Module"). */
  eyebrow?: string | undefined;
  title: string;
  /** Prose body rendered via WikiMarkdown. Omitted on pure-diagram slides. */
  bodyMarkdown?: string | undefined;
  /** Mermaid source for a diagram slide, rendered via MermaidDiagram. */
  mermaid?: string | undefined;
  /** Page this slide was derived from, so "open in reader" can jump there. */
  sourcePageId?: string | undefined;
  sourcePath?: string | undefined;
  /** Freshness of the source page (fresh/stale/outdated), for a subtle dot. */
  freshness?: string | undefined;
}

export interface PresentStep {
  id: string;
  order: number;
  title: string;
  /** One-line "why this matters" pulled from the guided-tour stop. */
  reason?: string | undefined;
  targetPath?: string | undefined;
  /** Reading body for the step, rendered via WikiMarkdown. */
  bodyMarkdown?: string | undefined;
  /** Deterministic reading-time estimate in minutes. */
  estMinutes: number;
  sourcePageId?: string | undefined;
  freshness?: string | undefined;
}

export interface PresentModel {
  repoName: string;
  deck: PresentSlide[];
  walkthrough: PresentStep[];
  /** Sum of every walkthrough step's estMinutes. */
  totalMinutes: number;
}

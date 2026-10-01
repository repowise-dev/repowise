// Present mode: a short narrated deck built entirely from already-generated
// wiki pages. No new generation, no network, no DB: `buildPresentModel`
// (build-present-model.ts) turns the loaded pages into this shape, and the
// overlay renders it.
//
// Kept framework-free (plain data) so every host renders the same deck.

import type { DocPage } from "@repowise-dev/types/docs";

/**
 * The deck's story, in order: what the repository is, how it fits together,
 * each major part, one flow end to end, and where to start reading.
 */
export type PresentSlideKind = "title" | "architecture" | "part" | "flow" | "start";

export interface StartFile {
  path: string;
  /** Page to open for this file, when the wiki has one. */
  pageId?: string | undefined;
}

/** A group of files to read first, with why they are worth opening. */
export interface StartGroup {
  /** The part the files belong to, when they came from a part's page. */
  label?: string | undefined;
  note?: string | undefined;
  files: StartFile[];
}

export interface PresentSlide {
  /** Stable id within the deck, used as a React key. */
  id: string;
  kind: PresentSlideKind;
  /** Small label above the title (e.g. "Part 2 of 5"). */
  eyebrow?: string | undefined;
  title: string;
  /** Whole-sentence prose rendered via WikiMarkdown. */
  body?: string | undefined;
  /** Mermaid source rendered via MermaidDiagram. */
  mermaid?: string | undefined;
  /** Step-named section headings of a part, in reading order. */
  steps?: string[] | undefined;
  /** Files to read first (the closing slide only). */
  start?: StartGroup[] | undefined;
  /** Page this slide was derived from, so "open in docs" can jump there. */
  sourcePageId?: string | undefined;
  /** Freshness of the source page (fresh/stale/outdated). */
  freshness?: string | undefined;
}

export interface PresentModel {
  repoName: string;
  slides: PresentSlide[];
}

/** The pages a deck is built from, as gathered by `loadPresentSource`. */
export interface PresentSource {
  overview: DocPage;
  /** The major parts, in the order the deck presents them. */
  parts: DocPage[];
  /** How many top-level parts the wiki has, of which `parts` is a selection. */
  totalParts: number;
  /** Page id per file path, so "where to start" entries can open their page. */
  pageIdByPath: ReadonlyMap<string, string>;
}

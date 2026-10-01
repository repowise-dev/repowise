// Build a Present deck from already-loaded wiki pages. Pure and synchronous:
// no LLM, no network, no DB.
//
// The deck tells one story in a fixed order: what the repository is (the
// overview's opening), how it fits together (the overview's diagram), one
// slide per major part (its opening, its own diagram, its step-named
// sections), one traced flow when a page carries a sequence diagram, and where
// to start reading. Every slide is optional except the title, so a page shape
// without diagrams or step sections yields a shorter deck, never a broken one.
// Prose is cut on sentence boundaries only; tables, lists and stat lines are
// never shown as slide text.

import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import { filterMarkdownByPersona } from "../docs/reader-persona";
import {
  diagramKind,
  extractMermaidBlocks,
  isDrawable,
  isProse,
  splitBlocks,
  splitOnH2,
  stripLeadingH1,
  wholeSentences,
  type SplitMarkdown,
} from "./split-markdown";
import type {
  PresentModel,
  PresentSlide,
  PresentSource,
  StartGroup,
} from "./types";

const TITLE_CHARS = 360;
const PART_CHARS = 420;
const CAPTION_CHARS = 280;
const SHORT_OPENING = 160;
const MAX_STEPS = 5;
const MAX_START_FILES = 8;

/** True when there is enough generated content to present. Reads only the page
 *  type, so it answers from a summary listing without fetching any bodies. */
export function canPresent(pages: readonly DocPageSummary[]): boolean {
  return pages.some((p) => p.page_type === "repo_overview");
}

/**
 * A readable repo name from the overview page title. Titles look like
 * "Repository Overview: name": prefer the part after the colon, then strip
 * stray "repository"/"overview" words.
 */
function deriveRepoName(overview: DocPage): string {
  const title = overview.title.trim();
  const afterColon = title.includes(":") ? (title.split(":").pop() ?? title).trim() : title;
  const cleaned = afterColon.replace(/\b(repository|repo|overview)\b/gi, "").replace(/\s+/g, " ").trim();
  return cleaned || afterColon || "This repository";
}

/** A page's human reading path: reference sections dropped, title removed. */
function readable(page: DocPage): SplitMarkdown {
  return splitOnH2(stripLeadingH1(filterMarkdownByPersona(page.content, "overview")));
}

/** Prose paragraphs of a region; a list's lead-in closes as a statement. */
function proseOf(markdown: string): string[] {
  return splitBlocks(markdown)
    .filter(isProse)
    .map((p) => p.replace(/:\s*$/, "."));
}

function firstProse(markdown: string): string | undefined {
  return proseOf(markdown)[0];
}

/**
 * The opening of a page: whole sentences from the first region (lead, then
 * each section) that has prose. A one-line lead-in borrows the paragraph after
 * it, so a slide says more than "It has three parts."
 */
function opening(doc: SplitMarkdown, maxChars: number): string | undefined {
  for (const region of [doc.lead, ...doc.sections.map((s) => s.body)]) {
    const [first, next] = proseOf(region);
    if (!first) continue;
    const text = wholeSentences(first, maxChars);
    if (text.length >= SHORT_OPENING || !next) return text;
    const more = wholeSentences(next, maxChars);
    return text.length + 1 + more.length <= maxChars ? `${text} ${more}` : text;
  }
  return undefined;
}

interface PlacedDiagram {
  chart: string;
  /** The H2 the diagram sits under; absent when it is in the lead. */
  heading?: string | undefined;
  /** The text of that section (or the lead), for a caption. */
  context: string;
}

function diagrams(doc: SplitMarkdown): PlacedDiagram[] {
  const regions = [
    { heading: undefined, body: doc.lead },
    ...doc.sections.map((s) => ({ heading: s.heading, body: s.body })),
  ];
  return regions.flatMap((r) =>
    extractMermaidBlocks(r.body)
      .filter(isDrawable)
      .map((chart) => ({ chart, heading: r.heading, context: r.body })),
  );
}

/** A caption from the diagram's own section, unless it opens like `shown`. */
function caption(placed: PlacedDiagram, shown: string | undefined): string | undefined {
  const para = firstProse(placed.context);
  if (!para) return undefined;
  const text = wholeSentences(para, CAPTION_CHARS);
  return shown && shown.startsWith(text.slice(0, 60)) ? undefined : text;
}

/** Step-named sections: the ones that close on a `Sources:` line. */
function steps(doc: SplitMarkdown): string[] {
  return doc.sections
    .filter((s) => {
      const last = s.body.trim().split("\n").pop() ?? "";
      return /^\W*sources\W*:/i.test(last.trim());
    })
    .map((s) => s.heading)
    .slice(0, MAX_STEPS);
}

const START_ITEM = /^\s*[-*+]\s+`([^`]+)`\s*(?:[-:\u2013\u2014]+\s*(.+))?$/;

/** The first entry of a part's "Where to start reading" list, if it has one. */
function partStart(
  part: DocPage,
  doc: SplitMarkdown,
  pageIdByPath: ReadonlyMap<string, string>,
): StartGroup | undefined {
  const section = doc.sections.find((s) => /^where to start/i.test(s.heading.trim()));
  const match = section?.body
    .split("\n")
    .map((line) => START_ITEM.exec(line))
    .find((m) => m !== null);
  const raw = match?.[1]?.trim();
  if (!raw) return undefined;
  // Entries are written relative to the part's directory; resolve when possible.
  const nested = `${part.target_path.replace(/\/+$/, "")}/${raw.replace(/^\.?\//, "")}`;
  const path = pageIdByPath.has(raw) ? raw : pageIdByPath.has(nested) ? nested : raw;
  return {
    label: part.title,
    note: match?.[2]?.trim() || undefined,
    files: [{ path, pageId: pageIdByPath.get(path) }],
  };
}

/**
 * The overview's guided-tour stops not already listed (nor repeated),
 * grouped where consecutive stops share a reason, up to `budget` files.
 */
function tourStart(
  overview: DocPage,
  pageIdByPath: ReadonlyMap<string, string>,
  listed: ReadonlySet<string>,
  budget: number,
): StartGroup[] {
  const raw = overview.metadata?.["guided_tour"];
  if (!Array.isArray(raw)) return [];
  const seen = new Set(listed);
  const groups: StartGroup[] = [];
  for (const stop of raw) {
    if (seen.size - listed.size >= budget) break;
    if (!stop || typeof stop !== "object") continue;
    const path = (stop as Record<string, unknown>)["target_path"];
    const reason = (stop as Record<string, unknown>)["reason"];
    if (typeof path !== "string" || !path || seen.has(path)) continue;
    seen.add(path);
    const note = typeof reason === "string" && reason ? reason : undefined;
    const file = { path, pageId: pageIdByPath.get(path) };
    const last = groups[groups.length - 1];
    if (last && !last.label && last.note === note) last.files.push(file);
    else groups.push({ note, files: [file] });
  }
  return groups;
}

export function buildPresentModel(source: PresentSource): PresentModel {
  const { overview, parts, totalParts, pageIdByPath } = source;
  const overviewDoc = readable(overview);
  const repoName = deriveRepoName(overview);
  const slides: PresentSlide[] = [];
  const shownCharts = new Set<string>();

  const summary = opening(overviewDoc, TITLE_CHARS);
  slides.push({
    id: "title",
    kind: "title",
    title: repoName,
    body: summary,
    sourcePageId: overview.id,
    freshness: overview.freshness_status,
  });

  const hero = diagrams(overviewDoc)[0];
  if (hero) {
    shownCharts.add(hero.chart);
    slides.push({
      id: "architecture",
      kind: "architecture",
      eyebrow: "Architecture",
      title: "How it fits together",
      body: caption(hero, summary),
      mermaid: hero.chart,
      sourcePageId: overview.id,
      freshness: overview.freshness_status,
    });
  }

  const partDocs = parts.map((page) => ({ page, doc: readable(page) }));
  partDocs.forEach(({ page, doc }, i) => {
    const chart = diagrams(doc)[0]?.chart;
    if (chart) shownCharts.add(chart);
    slides.push({
      id: `part-${page.id}`,
      kind: "part",
      eyebrow: `Part ${i + 1} of ${parts.length}`,
      title: page.title,
      body: opening(doc, PART_CHARS),
      mermaid: chart,
      steps: steps(doc),
      sourcePageId: page.id,
      freshness: page.freshness_status,
    });
  });

  // One traced flow: the first sequence diagram no earlier slide has shown.
  const flow = [...partDocs, { page: overview, doc: overviewDoc }]
    .flatMap(({ page, doc }) => diagrams(doc).map((d) => ({ page, doc, d })))
    .find(({ d }) => diagramKind(d.chart) === "sequencediagram" && !shownCharts.has(d.chart));
  if (flow) {
    slides.push({
      id: "flow",
      kind: "flow",
      eyebrow: "One flow, end to end",
      title: flow.d.heading ?? flow.page.title,
      body: caption(flow.d, opening(flow.doc, PART_CHARS)),
      mermaid: flow.d.chart,
      sourcePageId: flow.page.id,
      freshness: flow.page.freshness_status,
    });
  }

  const fromParts = partDocs
    .map(({ page, doc }) => partStart(page, doc, pageIdByPath))
    .filter((g): g is StartGroup => g !== undefined);
  // Parts name their own first file; the guided tour fills the rest.
  const listed = new Set(fromParts.flatMap((g) => g.files.map((f) => f.path)));
  const start = [
    ...fromParts,
    ...tourStart(overview, pageIdByPath, listed, MAX_START_FILES - listed.size),
  ];
  const omitted = totalParts - parts.length;
  const scope =
    omitted > 0
      ? `This deck covers ${parts.length} of the ${totalParts} top-level sections. The rest are in the documentation.`
      : undefined;
  if (start.length > 0 || scope) {
    slides.push({
      id: "start",
      kind: "start",
      eyebrow: "Reading list",
      title: "Where to start reading",
      body: scope,
      start,
    });
  }

  return { repoName, slides };
}

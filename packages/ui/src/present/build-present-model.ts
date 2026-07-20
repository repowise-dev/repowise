// Build a Present model (slide deck + guided walkthrough) from already-loaded
// wiki pages. Pure and synchronous: no LLM, no network, no DB. Every section is
// guarded so missing page types (e.g. no architecture diagram, index-only repos)
// degrade to a shorter deck instead of erroring.

import type { DocPage } from "@repowise-dev/types/docs";
import { filterMarkdownByPersona } from "../docs/reader-persona";
import {
  splitOnH2,
  stripLeadingH1,
  extractMermaidBlocks,
  clampProse,
  countWords,
} from "./split-markdown";
import type { PresentModel, PresentSlide, PresentStep } from "./types";

// Curation caps keep the deck tight and premium on large repos.
const MAX_LAYER_SLIDES = 5;
const MAX_MODULE_SLIDES = 5;
const MAX_TOUR_STOPS = 8;
const DECK_BODY_CHARS = 520;
const STEP_BODY_CHARS = 1100;

interface TourStop {
  order?: number | undefined;
  title?: string | undefined;
  target_path?: string | undefined;
  reason?: string | undefined;
  kind?: string | undefined;
}

function readTour(overview: DocPage | undefined): TourStop[] {
  const raw = overview?.metadata?.["guided_tour"];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((s): s is Record<string, unknown> => !!s && typeof s === "object")
    .map((s) => ({
      order: typeof s["order"] === "number" ? (s["order"] as number) : undefined,
      title: typeof s["title"] === "string" ? (s["title"] as string) : undefined,
      target_path:
        typeof s["target_path"] === "string" ? (s["target_path"] as string) : undefined,
      reason: typeof s["reason"] === "string" ? (s["reason"] as string) : undefined,
      kind: typeof s["kind"] === "string" ? (s["kind"] as string) : undefined,
    }));
}

function readLayerOrder(overview: DocPage | undefined): string[] {
  const raw = overview?.metadata?.["layer_order"];
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
}

function metaString(page: DocPage, key: string): string | undefined {
  const v = page.metadata?.[key];
  return typeof v === "string" ? v : undefined;
}

/**
 * A readable repo name from the overview page title. Titles look like
 * "Repository Overview: repowise" — prefer the part after the colon, then
 * strip stray "repository"/"overview" words, so the title slide reads "repowise"
 * not "This repository".
 */
function deriveRepoName(overview: DocPage | undefined, override?: string): string {
  const fromOverride = override?.trim();
  if (fromOverride) return fromOverride;
  if (!overview) return "This repository";
  const title = overview.title.trim();
  const afterColon = title.includes(":") ? (title.split(":").pop() ?? title).trim() : title;
  const cleaned = afterColon.replace(/\b(repository|repo|overview)\b/gi, "").replace(/\s+/g, " ").trim();
  return cleaned || afterColon || "This repository";
}

/** First real prose paragraph of a page (skips a leading heading). */
function firstProse(page: DocPage, maxChars: number): string {
  const filtered = stripLeadingH1(filterMarkdownByPersona(page.content, "overview"));
  const { lead, sections } = splitOnH2(filtered);
  const leadText = lead.trim();
  const source = leadText.length >= 40 ? leadText : (sections[0]?.body.trim() ?? leadText);
  const firstPara = source.split(/\n{2,}/)[0] ?? "";
  return clampProse(firstPara, maxChars);
}

/** Persona-filtered lead excerpt for a slide (prose + diagrams only). */
function slideBody(page: DocPage, maxChars: number): string {
  const filtered = stripLeadingH1(filterMarkdownByPersona(page.content, "overview"));
  const { lead, sections } = splitOnH2(filtered);
  let text = lead.trim();
  const first = sections[0];
  if (text.length < 60 && first) {
    text = `## ${first.heading}\n\n${first.body}`.trim();
  }
  return clampProse(text, maxChars);
}

function estimateMinutes(markdown: string): number {
  const words = countWords(markdown);
  return Math.min(12, Math.max(2, 2 + Math.ceil(words / 200)));
}

/** True when there is enough generated content to present. */
export function canPresent(pages: DocPage[]): boolean {
  return pages.some((p) => p.page_type === "repo_overview");
}

export function buildPresentModel(
  pages: DocPage[],
  opts: { repoName?: string } = {},
): PresentModel {
  const overview = pages.find((p) => p.page_type === "repo_overview");
  const repoName = deriveRepoName(overview, opts.repoName);

  const deck: PresentSlide[] = [];

  // 1 — Title
  deck.push({
    id: "title",
    kind: "title",
    eyebrow: "Repository",
    title: repoName,
    bodyMarkdown: overview ? firstProse(overview, 280) : undefined,
    sourcePageId: overview?.id,
    sourcePath: overview?.target_path,
    freshness: overview?.freshness_status,
  });

  // 2 — Architecture diagram slides
  const archPage = pages.find((p) => p.page_type === "architecture_diagram");
  if (archPage) {
    const charts = extractMermaidBlocks(archPage.content).slice(0, 2);
    charts.forEach((chart, i) => {
      deck.push({
        id: `arch-${i}`,
        kind: "diagram",
        eyebrow: "Architecture",
        title: charts.length > 1 ? `${archPage.title} (${i + 1})` : archPage.title,
        mermaid: chart,
        sourcePageId: archPage.id,
        sourcePath: archPage.target_path,
        freshness: archPage.freshness_status,
      });
    });
  }

  // 3 — Layers, ordered by the overview's layer_order when resolvable
  const layerOrder = readLayerOrder(overview);
  const rankOf = (name: string | undefined) => {
    if (!name) return Number.MAX_SAFE_INTEGER;
    const idx = layerOrder.indexOf(name);
    return idx === -1 ? Number.MAX_SAFE_INTEGER : idx;
  };
  const layerPages = pages
    .filter((p) => p.page_type === "layer_page")
    .sort((a, b) => rankOf(metaString(a, "layer_name")) - rankOf(metaString(b, "layer_name")))
    .slice(0, MAX_LAYER_SLIDES);
  for (const p of layerPages) {
    // Layer pages generated with the deterministic architecture diagram embed
    // it as a mermaid block; pair prose with diagram in a split slide. Older
    // indexes without a diagram degrade to a plain prose section.
    const layerChart = extractMermaidBlocks(p.content)[0];
    let body = slideBody(p, DECK_BODY_CHARS);
    // The diagram renders in its own column on a split slide, so keep any
    // embedded fence out of the prose body.
    if (layerChart) body = body.replace(/```mermaid[\s\S]*?```/g, "").trim();
    deck.push({
      id: `layer-${p.id}`,
      kind: layerChart ? "split" : "section",
      eyebrow: "Layer",
      title: p.title,
      bodyMarkdown: body,
      mermaid: layerChart,
      sourcePageId: p.id,
      sourcePath: p.target_path,
      freshness: p.freshness_status,
    });
  }

  // 4 — Modules, richest first (longest content is a decent proxy)
  const modulePages = pages
    .filter((p) => p.page_type === "module_page")
    .sort((a, b) => b.content.length - a.content.length)
    .slice(0, MAX_MODULE_SLIDES);
  for (const p of modulePages) {
    deck.push({
      id: `module-${p.id}`,
      kind: "section",
      eyebrow: "Module",
      title: p.title,
      bodyMarkdown: slideBody(p, DECK_BODY_CHARS),
      sourcePageId: p.id,
      sourcePath: p.target_path,
      freshness: p.freshness_status,
    });
  }

  // 5 — Where to start (guided-tour landmarks as one list slide)
  const tour = readTour(overview);
  if (tour.length > 0) {
    const items = tour
      .slice(0, MAX_TOUR_STOPS)
      .map((s) => {
        const label = s.title || s.target_path || "";
        const reason = s.reason ? ` — ${s.reason}` : "";
        return `- **${label}**${reason}`;
      })
      .join("\n");
    deck.push({
      id: "key-files",
      kind: "section",
      eyebrow: "Where to start",
      title: "Key files to read first",
      bodyMarkdown: items,
    });
  }

  // 6 — Closing
  deck.push({
    id: "closing",
    kind: "closing",
    eyebrow: "Keep exploring",
    title: "That's the tour",
    bodyMarkdown:
      "Open any page in the reader for the full detail, ask the assistant a question, or dive into the knowledge graph.",
  });

  // Walkthrough — one step per guided-tour stop, in order.
  const byPath = new Map(pages.map((p) => [p.target_path, p]));
  let walkthrough: PresentStep[] = tour.map((stop, i) => {
    const page = stop.target_path ? byPath.get(stop.target_path) : undefined;
    const body = page ? slideBody(page, STEP_BODY_CHARS) : "";
    const forEstimate = body || stop.reason || stop.title || "";
    return {
      id: `step-${i}`,
      order: stop.order ?? i + 1,
      title: stop.title || page?.title || stop.target_path || `Step ${i + 1}`,
      reason: stop.reason,
      targetPath: stop.target_path,
      bodyMarkdown: body,
      estMinutes: estimateMinutes(forEstimate),
      sourcePageId: page?.id,
      freshness: page?.freshness_status,
    };
  });

  // Fallback: no tour metadata (older/edge indexes) — walk the deck's section
  // slides so the walkthrough is never empty when a deck exists.
  if (walkthrough.length === 0) {
    walkthrough = deck
      .filter((s) => (s.kind === "section" || s.kind === "split") && s.bodyMarkdown)
      .map((s, i) => ({
        id: `step-${i}`,
        order: i + 1,
        title: s.title,
        reason: s.eyebrow,
        targetPath: s.sourcePath,
        bodyMarkdown: s.bodyMarkdown,
        estMinutes: estimateMinutes(s.bodyMarkdown ?? ""),
        sourcePageId: s.sourcePageId,
        freshness: s.freshness,
      }));
  }

  const totalMinutes = walkthrough.reduce((sum, s) => sum + s.estMinutes, 0);

  return { repoName, deck, walkthrough, totalMinutes };
}

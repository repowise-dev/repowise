import type { ComponentType } from "react";
import {
  Globe,
  LayoutGrid,
  FolderOpen,
  Sparkles,
  FileText,
  FileCode,
  RefreshCw,
  Server,
  Compass,
  Layers,
} from "lucide-react";
import type { DocPageSummary } from "@repowise-dev/types/docs";

export interface PageTypeConfig {
  label: string;
  icon: ComponentType<{ className?: string }>;
}

export const PAGE_TYPE_CONFIG: Record<string, PageTypeConfig> = {
  repo_overview: { label: "Overview", icon: Globe },
  architecture_diagram: { label: "Knowledge Graph", icon: LayoutGrid },
  layer_page: { label: "Layer", icon: Layers },
  module_page: { label: "Module", icon: FolderOpen },
  symbol_spotlight: { label: "Symbol", icon: Sparkles },
  file_page: { label: "File", icon: FileText },
  api_contract: { label: "API Contract", icon: FileCode },
  // "Cycle", not "SCC" — the tree labels these pages "Cycle: <path>", and the
  // filter chip is the control that narrows to them, so it uses the same word.
  scc_page: { label: "Cycle", icon: RefreshCw },
  infra_page: { label: "Infra", icon: Server },
  onboarding: { label: "Onboarding", icon: Compass },
};

export const ALL_PAGE_TYPES = Object.keys(PAGE_TYPE_CONFIG);

export function getPageTypeIcon(pageType: string): ComponentType<{ className?: string }> {
  return PAGE_TYPE_CONFIG[pageType]?.icon ?? FileText;
}

export function getPageTypeLabel(pageType: string): string {
  return PAGE_TYPE_CONFIG[pageType]?.label ?? pageType.replace(/_/g, " ");
}

/**
 * What to call a page, given the page rather than only its type.
 *
 * A chapter is a `module_page`, deliberately: it is a module page with a
 * better partition rather than an eleventh page type. So its type alone
 * labels it "Module", which is what the modules nested *under* it are called
 * too. `is_chapter` is the only thing that separates them, so anywhere a
 * reader is told what a page is, it has to be read.
 *
 * Takes a partial so a caller holding a summary row, a full page, or a search
 * hit can all pass what they have.
 */
export function getPageLabel(
  page: { page_type?: string; is_chapter?: boolean } | null | undefined,
): string {
  if (!page?.page_type) return "";
  if (page.is_chapter && page.page_type === "module_page") return "Chapter";
  return getPageTypeLabel(page.page_type);
}

// ---------------------------------------------------------------------------
// Model-written pages
// ---------------------------------------------------------------------------
//
// Every file / symbol / api / infra / scc / layer page renders from structure
// and is a template forever. Only the concept tree and onboarding are written
// by a model, so "does this page have prose yet" is a question that only makes
// sense for these four types. This mirrors core's MODEL_WRITTEN_PAGE_TYPES.

export const MODEL_WRITTEN_PAGE_TYPES = new Set([
  "module_page",
  "repo_overview",
  "architecture_diagram",
  "onboarding",
]);

/** True for the page types a model writes (the concept tree and onboarding).
 *  The regenerate affordance renders only on these; every other type is
 *  structural and has nothing to write into. */
export function isModelWrittenType(pageType: string | null | undefined): boolean {
  return !!pageType && MODEL_WRITTEN_PAGE_TYPES.has(pageType);
}

/** True when a model-written page is still a structural stub (no prose yet).
 *  Scoped to the model-written types: a stub carries `provider_name ===
 *  "template"`, a written page a real provider. Returns false for every
 *  structural page type, which is never a stub in this sense.
 *
 *  `metadata.model_free` marks a page whose subkind is rendered without a
 *  model by design — the glossary quotes mined definitions and has no prompt
 *  at all. It carries `provider_name === "template"` like a stub and means the
 *  opposite by it: the page is finished, and no model is ever going to write
 *  it. Without this check the tree marks it "a model has not written this page
 *  yet", offers a regenerate button that cannot help, and keeps the bulk
 *  generate affordance up on a complete wiki. */
export function isStubPage(
  page:
    | { page_type?: string; provider_name?: string; metadata?: Record<string, unknown> }
    | null
    | undefined,
): boolean {
  if (!page || !isModelWrittenType(page.page_type)) return false;
  if (page.metadata?.["model_free"]) return false;
  return page.provider_name === "template";
}

// ---------------------------------------------------------------------------
// Onboarding collection
// ---------------------------------------------------------------------------
//
// One slot — project_overview — is *promoted*: its
// content lives in the existing repo_overview page, tagged via
// `metadata.onboarding_slot`. The other five are dedicated
// `page_type === "onboarding"` pages with `metadata.subkind` discriminating
// them.
//
// A slot missing from this map is not merely unlabelled: `isOnboardingSlot`
// gates on it, so `getOnboardingSlot` returns null and the page drops out of
// the Onboarding folder entirely. Add every new slot here.
//
// This map is display text only. The *reading order* used to be duplicated
// here as an ONBOARDING_ORDER array kept in lockstep with `slots.py` by
// comment alone; it now arrives on the pages themselves as `display_order`,
// assigned once at generation time, so there is one ordering rather than two
// that can drift.

export const ONBOARDING_SLOT_TITLES = {
  project_overview: "Project Overview",
  getting_started: "Getting Started",
  key_concepts: "Key Concepts",
  how_it_works: "How It Works",
  active_landscape: "Active Landscape",
  glossary: "Glossary",

  // ---- Retired slots, kept only to label rows an old index still holds ----
  //
  // These three stopped being generated (see `slots.py`), and
  // `sweep_retired_pages` deletes their rows on the next docs update of any
  // kind. An index built before that release still holds them until the user
  // runs one, and `serve` does not.
  //
  // Only the *folder* view is affected — the default domain view places pages
  // by the stamped `parent_page_id` and labels them from `page.title`, so it
  // never consults this map. But `buildOnboardingFolder` does, and dropping a
  // slot from here does not hide such a row: `getOnboardingSlot` returns null
  // and `buildTree` lets the page fall through to path-based grouping, which
  // surfaces it as a stray top-level `onboarding/` directory beside the
  // Onboarding folder. That reads as a bug rather than a retirement, so they
  // stay listed and an un-swept row keeps the label it has always had.
  //
  // Ceiling: delete these three lines once no supported index predates the
  // sweep.
  guided_tour: "Guided Tour",
  codebase_map: "Codebase Map",
  development_guide: "Development Guide",
} as const;

export type OnboardingSlot = keyof typeof ONBOARDING_SLOT_TITLES;

function isOnboardingSlot(value: unknown): value is OnboardingSlot {
  // hasOwn, not `in`: `"toString" in obj` is true for every object, and a page
  // whose subkind happened to be a prototype member would then resolve to a
  // function where a title is expected.
  return typeof value === "string" && Object.hasOwn(ONBOARDING_SLOT_TITLES, value);
}

/**
 * Return the onboarding slot a page belongs to, or null if it isn't part of
 * the Onboarding collection.
 *
 * - Promoted pages (repo_overview, architecture_diagram) carry the slot in
 *   `metadata.onboarding_slot`.
 * - New onboarding pages (page_type === "onboarding") carry the slot in
 *   `metadata.subkind` (and also `metadata.onboarding_slot` as a mirror).
 */
export function getOnboardingSlot(page: DocPageSummary): OnboardingSlot | null {
  const meta = page.metadata ?? {};
  const fromSlot = meta["onboarding_slot"];
  if (isOnboardingSlot(fromSlot)) return fromSlot;
  if (page.page_type === "onboarding") {
    const subkind = meta["subkind"];
    if (isOnboardingSlot(subkind)) return subkind;
  }
  return null;
}

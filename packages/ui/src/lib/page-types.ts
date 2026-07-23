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
import type { DocPage } from "@repowise-dev/types/docs";

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
  scc_page: { label: "SCC", icon: RefreshCw },
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
 *  structural page type, which is never a stub in this sense. */
export function isStubPage(
  page: { page_type?: string; provider_name?: string } | null | undefined,
): boolean {
  if (!page || !isModelWrittenType(page.page_type)) return false;
  return page.provider_name === "template";
}

// ---------------------------------------------------------------------------
// Onboarding collection
// ---------------------------------------------------------------------------
//
// Two slots — project_overview and architecture_guide — are *promoted*: their
// content lives in the existing repo_overview / architecture_diagram pages,
// tagged via `metadata.onboarding_slot`. The other seven are dedicated
// `page_type === "onboarding"` pages with `metadata.subkind` discriminating
// them.
//
// This map is display text only. The *reading order* used to be duplicated
// here as an ONBOARDING_ORDER array kept in lockstep with `slots.py` by
// comment alone; it now arrives on the pages themselves as `display_order`,
// assigned once at generation time, so there is one ordering rather than two
// that can drift.

export const ONBOARDING_SLOT_TITLES = {
  project_overview: "Project Overview",
  architecture_guide: "Architecture Guide",
  guided_tour: "Guided Tour",
  getting_started: "Getting Started",
  codebase_map: "Codebase Map",
  key_concepts: "Key Concepts",
  how_it_works: "How It Works",
  development_guide: "Development Guide",
  active_landscape: "Active Landscape",
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
export function getOnboardingSlot(page: DocPage): OnboardingSlot | null {
  const meta = page.metadata ?? {};
  const fromSlot = meta["onboarding_slot"];
  if (isOnboardingSlot(fromSlot)) return fromSlot;
  if (page.page_type === "onboarding") {
    const subkind = meta["subkind"];
    if (isOnboardingSlot(subkind)) return subkind;
  }
  return null;
}

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
// Deterministic ("auto") pages
// ---------------------------------------------------------------------------
//
// The Phase G coverage tail gives every parsed source file a template-generated
// page (zero LLM) so the whole tree is browsable and retrievable. These are
// factual but terse; the UI badges them "Auto" and the docs tree groups them so
// human browsing of the AI-written pages stays clean.

/** Fields any surface needs to recognise a deterministic page. All optional so
 *  older payloads (DocPage / FileWikiPageRef / SearchResultResponse) type-check. */
export interface DeterministicMarker {
  is_deterministic?: boolean;
  doc_tier?: number | null;
  provider_name?: string;
}

/**
 * True when a page is a deterministic (auto, no-LLM) page. Prefers the flat
 * `is_deterministic` flag; falls back to `doc_tier >= 2` or the `template`
 * provider for rows/payloads that only carry those.
 */
export function isDeterministicPage(page: DeterministicMarker | null | undefined): boolean {
  if (!page) return false;
  if (page.is_deterministic) return true;
  if (typeof page.doc_tier === "number" && page.doc_tier >= 2) return true;
  return page.provider_name === "template";
}

export const DETERMINISTIC_BADGE_LABEL = "Auto";
export const DETERMINISTIC_BADGE_TITLE =
  "Auto-documented from code structure (no AI). Factual but terse.";

// ---------------------------------------------------------------------------
// Onboarding collection
// ---------------------------------------------------------------------------
//
// Keep this list in lockstep with the Python side
// (`packages/core/src/repowise/core/generation/onboarding/slots.py`).
// Two slots — project_overview and architecture_guide — are *promoted*: their
// content lives in the existing repo_overview / architecture_diagram pages,
// tagged via `metadata.onboarding_slot`. The other six are dedicated
// `page_type === "onboarding"` pages with `metadata.subkind` discriminating
// them.

export const ONBOARDING_ORDER = [
  "project_overview",
  "architecture_guide",
  "guided_tour",
  "getting_started",
  "codebase_map",
  "key_concepts",
  "how_it_works",
  "development_guide",
  "active_landscape",
] as const;

export type OnboardingSlot = (typeof ONBOARDING_ORDER)[number];

export const ONBOARDING_SLOT_TITLES: Record<OnboardingSlot, string> = {
  project_overview: "Project Overview",
  architecture_guide: "Architecture Guide",
  guided_tour: "Guided Tour",
  getting_started: "Getting Started",
  codebase_map: "Codebase Map",
  key_concepts: "Key Concepts",
  how_it_works: "How It Works",
  development_guide: "Development Guide",
  active_landscape: "Active Landscape",
};

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
  if (typeof fromSlot === "string" && (ONBOARDING_ORDER as readonly string[]).includes(fromSlot)) {
    return fromSlot as OnboardingSlot;
  }
  if (page.page_type === "onboarding") {
    const subkind = meta["subkind"];
    if (
      typeof subkind === "string" &&
      (ONBOARDING_ORDER as readonly string[]).includes(subkind)
    ) {
      return subkind as OnboardingSlot;
    }
  }
  return null;
}

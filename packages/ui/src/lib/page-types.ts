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
} from "lucide-react";
import type { DocPage } from "@repowise-dev/types/docs";

export interface PageTypeConfig {
  label: string;
  icon: ComponentType<{ className?: string }>;
}

export const PAGE_TYPE_CONFIG: Record<string, PageTypeConfig> = {
  repo_overview: { label: "Overview", icon: Globe },
  architecture_diagram: { label: "Architecture", icon: LayoutGrid },
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

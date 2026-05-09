import type { ComponentType } from "react";
import {
  Globe,
  LayoutGrid,
  FolderOpen,
  Sparkles,
  FileText,
  FileCode,
  RefreshCw,
  ArrowLeftRight,
  Server,
  GitCommit,
} from "lucide-react";

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
  cross_package: { label: "Cross-Package", icon: ArrowLeftRight },
  infra_page: { label: "Infra", icon: Server },
  diff_summary: { label: "Diff Summary", icon: GitCommit },
};

export const ALL_PAGE_TYPES = Object.keys(PAGE_TYPE_CONFIG);

export function getPageTypeIcon(pageType: string): ComponentType<{ className?: string }> {
  return PAGE_TYPE_CONFIG[pageType]?.icon ?? FileText;
}

export function getPageTypeLabel(pageType: string): string {
  return PAGE_TYPE_CONFIG[pageType]?.label ?? pageType.replace(/_/g, " ");
}

/**
 * Single source of truth for app navigation. Both the desktop sidebar and
 * the mobile nav consume these — the two surfaces must never diverge again.
 *
 * Repo IA: Overview and Chat unlabelled at the top, then four collapsible
 * groups by the question a reader brings: Understand (Docs, Architecture,
 * Knowledge Graph, Files), Code quality (Code Health, Refactoring, Tests,
 * Security), People & History (Contributors, Commits, Decisions), and
 * Settings pinned last. Chat sits second because it is the fastest route to
 * an answer about anything below it, not because it is the second most
 * visited page.
 */

import {
  Activity,
  BarChart3,
  BookOpen,
  Boxes,
  DollarSign,
  FlaskConical,
  FolderTree,
  GitCommitHorizontal,
  GitMerge,
  HeartPulse,
  LayoutDashboard,
  Layers,
  Lightbulb,
  Link2,
  MessageSquare,
  ScanSearch,
  Settings,
  Shield,
  ShieldCheck,
  Users,
  Waypoints,
  Wrench,
} from "lucide-react";

export interface NavItem {
  /**
   * English label. Kept as the canonical value for non-visual uses — command
   * palette keyword matching and the `title`/aria fallback — so a translated
   * UI never changes what a search query has to match.
   */
  label: string;
  /** Message key under the `nav` namespace; what the UI actually renders. */
  labelKey: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
}

export interface NavGroup {
  /** Stable id; keys the group's collapsed state in the sidebar. */
  id: string;
  /** Optional section label rendered above the items. Labelled groups collapse. */
  label?: string;
  /** Message key under `nav` for the group label. */
  labelKey?: string;
  items: NavItem[];
}

export const GLOBAL_NAV: NavItem[] = [
  { label: "Dashboard", labelKey: "dashboard", href: "/", icon: LayoutDashboard },
  { label: "Settings", labelKey: "settings", href: "/settings", icon: Settings },
];

export const WORKSPACE_NAV: NavItem[] = [
  { label: "Overview", labelKey: "overview", href: "/workspace", icon: Layers, exact: true },
  { label: "System Map", labelKey: "systemMap", href: "/workspace/system-map", icon: Waypoints },
  { label: "Conformance", labelKey: "conformance", href: "/workspace/conformance", icon: ShieldCheck },
  { label: "Contracts", labelKey: "contracts", href: "/workspace/contracts", icon: Link2 },
  { label: "Co-Changes", labelKey: "coChanges", href: "/workspace/co-changes", icon: GitMerge },
];

export function repoNavGroups(repoId: string): NavGroup[] {
  const base = `/repos/${repoId}`;
  return [
    {
      id: "start",
      items: [
        { label: "Overview", labelKey: "overview", href: `${base}/overview`, icon: Activity },
        { label: "Chat", labelKey: "chat", href: `${base}/chat`, icon: MessageSquare },
      ],
    },
    {
      id: "understand",
      label: "Understand",
      labelKey: "groupUnderstand",
      items: [
        { label: "Docs", labelKey: "docs", href: `${base}/docs`, icon: BookOpen },
        { label: "Architecture", labelKey: "architecture", href: `${base}/architecture`, icon: Boxes },
        { label: "Knowledge Graph", labelKey: "knowledgeGraph", href: `${base}/knowledge-graph`, icon: ScanSearch },
        { label: "Files", labelKey: "files", href: `${base}/files`, icon: FolderTree },
      ],
    },
    {
      id: "quality",
      label: "Code quality",
      labelKey: "groupCodeQuality",
      items: [
        { label: "Code Health", labelKey: "codeHealth", href: `${base}/code-health`, icon: HeartPulse },
        { label: "Refactoring", labelKey: "refactoring", href: `${base}/refactoring`, icon: Wrench },
        { label: "Tests", labelKey: "tests", href: `${base}/tests`, icon: FlaskConical },
        { label: "Security", labelKey: "security", href: `${base}/security`, icon: Shield },
      ],
    },
    {
      id: "people",
      label: "People & History",
      labelKey: "groupPeopleAndHistory",
      items: [
        { label: "Ownership", labelKey: "contributors", href: `${base}/owners`, icon: Users },
        { label: "Commits", labelKey: "commits", href: `${base}/commits`, icon: GitCommitHorizontal },
        { label: "Decisions", labelKey: "decisions", href: `${base}/decisions`, icon: Lightbulb },
      ],
    },
    {
      id: "settings",
      label: "Settings",
      labelKey: "groupSettings",
      items: [
        { label: "Stats", labelKey: "stats", href: `${base}/stats`, icon: BarChart3 },
        { label: "Usage & savings", labelKey: "usageAndSavings", href: `${base}/costs`, icon: DollarSign },
        { label: "Settings", labelKey: "settings", href: `${base}/settings`, icon: Settings },
      ],
    },
  ];
}

/** Flat repo nav list (command palette, breadcrumb fallbacks, …). */
export function repoNavItems(repoId: string): NavItem[] {
  return repoNavGroups(repoId).flatMap((g) => g.items);
}

export function isNavItemActive(item: NavItem, pathname: string): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

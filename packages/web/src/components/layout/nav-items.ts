/**
 * Single source of truth for app navigation. Both the desktop sidebar and
 * the mobile nav consume these — the two surfaces must never diverge again.
 *
 * Repo IA (6 groups + Settings pinned last):
 *   Overview · Docs · Architecture · Knowledge Graph · Code Health ·
 *   People & History · Chat
 */

import {
  Activity,
  BarChart3,
  BookOpen,
  Boxes,
  DollarSign,
  GitCommitHorizontal,
  GitMerge,
  HeartPulse,
  LayoutDashboard,
  Layers,
  Lightbulb,
  Link2,
  MessageSquare,
  Settings,
  ShieldCheck,
  Users,
  Waypoints,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
}

export interface NavGroup {
  /** Optional section label rendered above the items. */
  label?: string;
  items: NavItem[];
}

export const GLOBAL_NAV: NavItem[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Settings", href: "/settings", icon: Settings },
];

export const WORKSPACE_NAV: NavItem[] = [
  { label: "Overview", href: "/workspace", icon: Layers, exact: true },
  { label: "System Map", href: "/workspace/system-map", icon: Waypoints },
  { label: "Conformance", href: "/workspace/conformance", icon: ShieldCheck },
  { label: "Contracts", href: "/workspace/contracts", icon: Link2 },
  { label: "Co-Changes", href: "/workspace/co-changes", icon: GitMerge },
];

export function repoNavGroups(repoId: string): NavGroup[] {
  const base = `/repos/${repoId}`;
  return [
    {
      items: [
        { label: "Overview", href: `${base}/overview`, icon: Activity },
        { label: "Docs", href: `${base}/docs`, icon: BookOpen },
        { label: "Architecture", href: `${base}/architecture`, icon: Boxes },
        { label: "Knowledge Graph", href: `${base}/knowledge-graph`, icon: Waypoints },
        { label: "Code Health", href: `${base}/code-health`, icon: HeartPulse },
      ],
    },
    {
      label: "People & History",
      items: [
        { label: "Commits", href: `${base}/commits`, icon: GitCommitHorizontal },
        { label: "Contributors", href: `${base}/owners`, icon: Users },
        { label: "Decisions", href: `${base}/decisions`, icon: Lightbulb },
      ],
    },
    {
      items: [{ label: "Chat", href: `${base}/chat`, icon: MessageSquare }],
    },
    {
      label: "Settings",
      items: [
        { label: "Stats", href: `${base}/stats`, icon: BarChart3 },
        { label: "Usage & savings", href: `${base}/costs`, icon: DollarSign },
        { label: "Settings", href: `${base}/settings`, icon: Settings },
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

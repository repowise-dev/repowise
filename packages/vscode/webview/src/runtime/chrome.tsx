/**
 * Cross-panel navigation chrome shared by every editor-tab panel. Panels are
 * otherwise dead-end tabs; this slim header gives each one a way back to the
 * sidebar Home and a one-click switch to any sibling dashboard, so the whole
 * extension reads as one surface instead of a scatter of disconnected views.
 */

import type { ComponentType } from "react";
import {
  Activity,
  BookOpen,
  GitBranch,
  Home,
  Layers,
  Scale,
  Settings2,
  Share2,
  Wrench,
} from "lucide-react";
import type { PanelViewId } from "../../../src/shared/webviewMessages";
import type { WebviewHost } from "./rpc";

interface NavItem {
  view: PanelViewId;
  title: string;
  icon: ComponentType<{ className?: string }>;
}

/** The dashboards a panel can switch between, in the sidebar Home order. */
const NAV: NavItem[] = [
  { view: "health", title: "Code Health", icon: Activity },
  { view: "architecture", title: "Architecture", icon: Layers },
  { view: "graph", title: "Knowledge Graph", icon: Share2 },
  { view: "refactoring", title: "Refactoring", icon: Wrench },
  { view: "decisions", title: "Decisions", icon: Scale },
  { view: "docs", title: "Docs", icon: BookOpen },
  { view: "risk", title: "Review", icon: GitBranch },
];

/**
 * One underline tab row, as on the web. Every destination keeps its icon; the
 * labels need about 1040px, so below that only the open panel keeps its label
 * and the others are icons with a tooltip and an accessible name. The row
 * never shows a scrollbar: it scrolls quietly only below ~520px, where even
 * the icons no longer fit. Tab focus rings are drawn inside the tab (theme-bridge.css),
 * since the scrolling row clips anything drawn outside it.
 */
export function PanelChrome({ view, host }: { view: PanelViewId; host: WebviewHost }) {
  return (
    <header className="@container sticky top-0 z-30 shrink-0 border-b border-[var(--color-border-default)] bg-[var(--color-bg-root)]">
      <div className="flex h-10 items-stretch gap-1 px-2">
        <button
          type="button"
          onClick={() => host.focusHome()}
          title="Back to Repowise home"
          aria-label="Back to Repowise home"
          className="my-1.5 flex shrink-0 items-center gap-1.5 rounded-md px-1.5 text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]"
        >
          <Home className="h-4 w-4" />
          <span className="hidden text-xs font-semibold @[1040px]:inline">Repowise</span>
        </button>
        <span aria-hidden className="mx-1 my-3 w-px shrink-0 bg-[var(--color-border-default)]" />
        <nav
          aria-label="Dashboards"
          data-panel-nav=""
          className="flex min-w-0 flex-1 items-stretch gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {NAV.map((item) => (
            <Tab
              key={item.view}
              item={item}
              active={item.view === view}
              onClick={() => (item.view === view ? undefined : host.openView(item.view))}
            />
          ))}
        </nav>
        <button
          type="button"
          onClick={() => (view === "settings" ? undefined : host.openView("settings"))}
          title="Repowise settings"
          aria-label="Repowise settings"
          aria-current={view === "settings" ? "page" : undefined}
          className={
            "shrink-0 border-b-2 px-2 transition-colors " +
            (view === "settings"
              ? "border-[var(--color-tab-active)] text-[var(--color-text-primary)]"
              : "border-transparent text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]")
          }
        >
          <Settings2 className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}

function Tab({
  item,
  active,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  onClick: () => void;
}) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      aria-label={item.title}
      title={item.title}
      className={
        "flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-2 text-xs font-medium transition-colors " +
        (active
          ? "border-[var(--color-tab-active)] text-[var(--color-text-primary)]"
          : "border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]")
      }
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className={active ? "inline" : "hidden @[1040px]:inline"}>{item.title}</span>
    </button>
  );
}

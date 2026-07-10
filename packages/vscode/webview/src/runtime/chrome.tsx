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
  { view: "health", title: "Health", icon: Activity },
  { view: "architecture", title: "Architecture", icon: Layers },
  { view: "graph", title: "Graph", icon: Share2 },
  { view: "refactoring", title: "Refactoring", icon: Wrench },
  { view: "decisions", title: "Decisions", icon: Scale },
  { view: "docs", title: "Docs", icon: BookOpen },
  { view: "risk", title: "Change Risk", icon: GitBranch },
];

export function PanelChrome({ view, host }: { view: PanelViewId; host: WebviewHost }) {
  return (
    <header className="sticky top-0 z-30 flex h-11 shrink-0 items-center gap-1 border-b border-[var(--color-border-default)] bg-[var(--color-bg-root)] px-2">
      <button
        type="button"
        onClick={() => host.focusHome()}
        title="Back to Repowise home"
        className="flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-surface)] hover:text-[var(--color-text-primary)]"
      >
        <Home className="h-4 w-4" />
        <span className="hidden text-[11px] font-semibold tracking-wide sm:inline">Repowise</span>
      </button>
      <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-[var(--color-border-default)]" />
      <nav aria-label="Dashboards" className="flex min-w-0 items-center gap-0.5 overflow-x-auto">
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
        aria-current={view === "settings" ? "page" : undefined}
        className={
          "ml-auto shrink-0 rounded-md p-1.5 transition-colors " +
          (view === "settings"
            ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
            : "text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-surface)] hover:text-[var(--color-text-primary)]")
        }
      >
        <Settings2 className="h-4 w-4" />
      </button>
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
      title={item.title}
      className={
        "flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors " +
        (active
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-surface)] hover:text-[var(--color-text-primary)]")
      }
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="hidden md:inline">{item.title}</span>
    </button>
  );
}

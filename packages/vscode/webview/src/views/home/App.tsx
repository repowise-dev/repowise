/**
 * Sidebar Home: the face of the extension. A hero card with the repo's
 * hotspot-health score and trend, an index-freshness row with a one-click
 * update, and a launcher card per dashboard panel so everything the extension
 * can show is discoverable from the first click on the activity-bar icon.
 * Designed for the sidebar's ~300px width; the dashboards themselves open as
 * editor tabs via `host.openView`.
 */

import { useCallback, useEffect, useState, type ComponentType } from "react";
import {
  Activity,
  BookOpen,
  ChevronRight,
  GitBranch,
  Layers,
  Monitor,
  Moon,
  RefreshCw,
  Scale,
  Share2,
  Sun,
  Wrench,
} from "lucide-react";
import type {
  HomeSummary,
  PanelViewId,
  ThemePreference,
} from "../../../../src/shared/webviewMessages";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";
import {
  getThemeKind,
  getThemePreference,
  setThemePreference,
  subscribeTheme,
  type ThemeKind,
} from "../../runtime/theme";
import owlDarkTheme from "../../assets/owl-dark-theme.png";
import owlLightTheme from "../../assets/owl-light-theme.png";

/** 0-10 score to its signal color; the same bands the risk view uses, inverted. */
function scoreColor(score: number | null): string {
  if (score == null) return "var(--color-text-tertiary)";
  if (score >= 7.5) return "var(--color-success)";
  if (score >= 5) return "var(--color-warning)";
  return "var(--color-error)";
}

/** Compact relative time for the freshness row ("3h ago"). */
function timeAgo(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days < 30 ? `${days}d ago` : new Date(then).toLocaleDateString();
}

function short(commit: string | null): string | null {
  return commit ? commit.slice(0, 7) : null;
}

interface CardSpec {
  view: PanelViewId;
  title: string;
  icon: ComponentType<{ className?: string }>;
  /** Live micro-stat when the summary carries one, otherwise a static hook. */
  subtitle: (summary: HomeSummary | null, defaultBranch: string | null) => string;
}

const CARDS: CardSpec[] = [
  {
    view: "health",
    title: "Health Dashboard",
    icon: Activity,
    subtitle: (s) =>
      s?.health
        ? `${s.health.openFindings.toLocaleString()} open findings · ${s.health.fileCount.toLocaleString()} files`
        : "Score map, trends, and the churn quadrant",
  },
  {
    view: "architecture",
    title: "Architecture Map",
    icon: Layers,
    subtitle: () => "Modules and layers on one canvas",
  },
  {
    view: "graph",
    title: "Knowledge Graph",
    icon: Share2,
    subtitle: () => "Dependencies, communities, execution flows",
  },
  {
    view: "refactoring",
    title: "Refactoring Plans",
    icon: Wrench,
    subtitle: (s) =>
      s?.counts.refactoringPlans != null
        ? `${s.counts.refactoringPlans.toLocaleString()} ranked plans`
        : "Server-ranked refactoring opportunities",
  },
  {
    view: "decisions",
    title: "Decision Timeline",
    icon: Scale,
    subtitle: (s) =>
      s?.counts.decisions != null
        ? `${s.counts.decisions.toLocaleString()} decision records`
        : "Architectural decision records",
  },
  {
    view: "docs",
    title: "Docs",
    icon: BookOpen,
    subtitle: () => "Browse the generated wiki",
  },
  {
    view: "risk",
    title: "Branch Risk",
    icon: GitBranch,
    subtitle: (_s, defaultBranch) => `Score this branch against ${defaultBranch ?? "main"}`,
  },
];

export function App({ host, repo, refreshToken }: ViewProps<"home">) {
  const [summary, setSummary] = useState<HomeSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    host.api
      .homeSummary()
      .then((s) => {
        setSummary(s);
        setLoading(false);
      })
      .catch(() => {
        // The launcher stays useful without stats; sections degrade to their
        // static subtitles and the hero shows its empty state.
        setSummary(null);
        setLoading(false);
      });
  }, [host]);

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  // The host acks update completion whether it succeeded or failed; a success
  // also lands as a refresh, so this reload mostly re-reads the fresh cache.
  useEffect(
    () =>
      host.onUpdateDone(() => {
        setUpdating(false);
        load();
      }),
    [host, load],
  );

  const runUpdate = () => {
    if (updating) return;
    setUpdating(true);
    host.updateIndex();
  };

  return (
    <div className="min-h-screen space-y-3 bg-[var(--color-bg-root)] p-3">
      <Hero
        repoName={repo.name}
        branch={summary?.freshness.branch ?? null}
        health={summary?.health ?? null}
        loading={loading && !summary}
      />
      <Freshness
        freshness={summary?.freshness ?? null}
        updating={updating}
        onUpdate={runUpdate}
      />
      <nav aria-label="Dashboards" className="space-y-1.5">
        <p className="px-1 pt-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Explore
        </p>
        {CARDS.map((card) => (
          <LauncherCard
            key={card.view}
            spec={card}
            subtitle={card.subtitle(summary, repo.defaultBranch)}
            onOpen={() => host.openView(card.view)}
          />
        ))}
      </nav>
      <Footer host={host} />
    </div>
  );
}

/** Brand mark plus the theme switcher. The preference applies to every
 *  Repowise webview and persists via the host; "auto" follows the editor. */
function Footer({ host }: { host: WebviewHost }) {
  const [pref, setPref] = useState<ThemePreference>(getThemePreference());
  const [kind, setKind] = useState<ThemeKind>(getThemeKind());
  useEffect(() => subscribeTheme(setKind), []);

  const choose = (next: ThemePreference) => {
    setPref(next);
    // Applied locally for instant feedback; the host persists it and pushes
    // theme-changed to every other open Repowise view.
    setThemePreference(next);
    host.setTheme(next);
  };

  const options: { value: ThemePreference; icon: ComponentType<{ className?: string }>; label: string }[] = [
    { value: "auto", icon: Monitor, label: "Follow editor theme" },
    { value: "light", icon: Sun, label: "Light" },
    { value: "dark", icon: Moon, label: "Dark" },
  ];

  return (
    <footer className="flex items-center justify-between px-1 pb-1 pt-2">
      <span className="flex items-center gap-1.5">
        <img
          src={kind === "dark" ? owlDarkTheme : owlLightTheme}
          alt=""
          className="h-4 w-4"
        />
        <span className="text-[11px] font-semibold tracking-wide text-[var(--color-text-tertiary)]">
          Repowise
        </span>
      </span>
      <div
        role="group"
        aria-label="Theme"
        className="inline-flex rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-0.5"
      >
        {options.map(({ value, icon: Icon, label }) => (
          <button
            key={value}
            type="button"
            onClick={() => choose(value)}
            aria-pressed={pref === value}
            title={label}
            className={
              pref === value
                ? "rounded-md bg-[var(--color-accent-primary)] p-1 text-[var(--color-text-on-accent)]"
                : "rounded-md p-1 text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
            }
          >
            <Icon className="h-3 w-3" />
          </button>
        ))}
      </div>
    </footer>
  );
}

function Hero({
  repoName,
  branch,
  health,
  loading,
}: {
  repoName: string;
  branch: string | null;
  health: HomeSummary["health"];
  loading: boolean;
}) {
  // Quiet or small repos can gate hotspot scoring off; the hero then falls
  // back to the repo average rather than claiming there is no health data.
  const usingHotspot = health?.hotspot != null;
  const headline = health?.hotspot ?? health?.average ?? null;
  const color = scoreColor(headline);
  return (
    <section
      aria-label="Repository health"
      className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
          {repoName}
        </p>
        {branch ? (
          <span className="flex min-w-0 shrink items-center gap-1 text-[11px] text-[var(--color-text-tertiary)]">
            <GitBranch className="h-3 w-3 shrink-0" />
            <span className="truncate font-mono">{branch}</span>
          </span>
        ) : null}
      </div>

      {loading ? (
        <div className="mt-3 space-y-2" aria-hidden>
          <div className="h-9 w-24 animate-pulse rounded-md bg-[var(--color-bg-inset)]" />
          <div className="h-3 w-40 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        </div>
      ) : headline != null ? (
        <>
          <div className="mt-3 flex items-end gap-2">
            <span className="text-4xl font-bold leading-none tracking-tight" style={{ color }}>
              {headline.toFixed(1)}
            </span>
            <span className="pb-0.5 text-sm text-[var(--color-text-tertiary)]">/ 10</span>
            {usingHotspot ? <DeltaChip delta={health?.hotspotDelta ?? null} /> : null}
          </div>
          <p className="mt-1.5 text-[11px] text-[var(--color-text-secondary)]">
            {usingHotspot ? "Hotspot health" : "Average health"}
            {usingHotspot && health?.average != null
              ? ` · average ${health.average.toFixed(1)}`
              : ""}
            {` · ${(health?.fileCount ?? 0).toLocaleString()} files`}
          </p>
          {usingHotspot && (health?.history.length ?? 0) >= 2 ? (
            <Sparkline values={health?.history ?? []} color={color} />
          ) : null}
        </>
      ) : (
        <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
          No health data yet. Scores appear after the first full index.
        </p>
      )}
    </section>
  );
}

function DeltaChip({ delta }: { delta: number | null }) {
  if (delta == null || delta === 0) return null;
  const up = delta > 0;
  return (
    <span
      className="mb-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
      style={{
        color: up ? "var(--color-success)" : "var(--color-error)",
        backgroundColor: "var(--color-bg-inset)",
      }}
      title="Hotspot health change since the previous snapshot"
    >
      {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}
    </span>
  );
}

/** Tiny inline trend line; the hero already labels it, so it is decorative. */
function Sparkline({ values, color }: { values: number[]; color: string }) {
  const width = 100;
  const height = 24;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - 3 - ((v - min) / span) * (height - 6)).toFixed(1)}`)
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="mt-2 h-6 w-full"
      aria-hidden
    >
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" opacity="0.9" />
    </svg>
  );
}

function Freshness({
  freshness,
  updating,
  onUpdate,
}: {
  freshness: HomeSummary["freshness"] | null;
  updating: boolean;
  onUpdate: () => void;
}) {
  const stale = freshness?.stale ?? false;
  const indexed = short(freshness?.indexedCommit ?? null);
  const live = short(freshness?.liveCommit ?? null);
  const dotColor = stale ? "var(--color-warning)" : "var(--color-success)";

  let detail = "";
  if (stale && indexed && live) detail = `${indexed} → ${live}`;
  else if (indexed) {
    const ago = freshness?.lastIndexedAt ? timeAgo(freshness.lastIndexedAt) : "";
    detail = ago ? `${indexed} · indexed ${ago}` : indexed;
  }

  return (
    <section
      aria-label="Index freshness"
      className="flex items-center gap-2.5 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2.5"
    >
      <span
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ backgroundColor: dotColor }}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-[var(--color-text-primary)]">
          {updating ? "Updating index…" : stale ? "Index behind checkout" : "Index up to date"}
        </p>
        {detail ? (
          <p className="truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
            {detail}
          </p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={onUpdate}
        disabled={updating}
        title="Run an incremental index update"
        className={
          stale && !updating
            ? "flex shrink-0 items-center gap-1.5 rounded-lg bg-[var(--color-accent-primary)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text-on-accent)] transition-opacity hover:opacity-90"
            : "flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-default disabled:opacity-60"
        }
      >
        <RefreshCw className={updating ? "h-3 w-3 animate-spin" : "h-3 w-3"} />
        {updating ? "Updating" : "Update"}
      </button>
    </section>
  );
}

function LauncherCard({
  spec,
  subtitle,
  onOpen,
}: {
  spec: CardSpec;
  subtitle: string;
  onOpen: () => void;
}) {
  const Icon = spec.icon;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full items-center gap-3 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2.5 text-left transition-colors hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-elevated)]"
    >
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]">
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-[var(--color-text-primary)]">
          {spec.title}
        </span>
        <span className="block truncate text-[11px] text-[var(--color-text-tertiary)]">
          {subtitle}
        </span>
      </span>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}

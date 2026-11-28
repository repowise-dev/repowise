/**
 * Sidebar Home: the face of the extension, read top to bottom in the order a
 * person needs it. First the state of the repo (its health and whether the
 * index is current, with the one update action), then what to look at next,
 * then every dashboard as a quiet list. Designed for the sidebar's ~300-400px
 * width; the dashboards themselves open as editor tabs via `host.openView`.
 *
 * Grouping is hairlines and rhythm. Nothing here is a card: the statistics are
 * a hairline ribbon and the destinations are rows. Icons are muted ink, the
 * one primary action is the ink Update button when the index is behind, and
 * orange is left to focus and links, as on the web.
 */

import { useCallback, useEffect, useState, type ComponentType } from "react";
import {
  Activity,
  BookOpen,
  ChevronRight,
  GitBranch,
  Layers,
  RefreshCw,
  Scale,
  Settings2,
  Share2,
  Wrench,
} from "lucide-react";
import { EmptyState } from "@repowise-dev/ui/shared";
import { Segmented } from "@repowise-dev/ui/shared/segmented";
import { Button } from "@repowise-dev/ui/ui";
import { healthMark } from "@repowise-dev/ui/health/tokens";
import { formatScore } from "@repowise-dev/types/health";
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

const SITE_URL = "https://repowise.dev/?utm_source=vscode";

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

interface Destination {
  view: PanelViewId;
  title: string;
  icon: ComponentType<{ className?: string }>;
  /** A cheap, meaningful count from the summary, shown beside the name. */
  count?: (summary: HomeSummary | null) => string | null;
}

const DESTINATIONS: Destination[] = [
  {
    view: "health",
    title: "Code Health",
    icon: Activity,
    count: (s) =>
      s?.health ? `${s.health.openFindings.toLocaleString()} open findings` : null,
  },
  { view: "architecture", title: "Architecture", icon: Layers },
  { view: "graph", title: "Knowledge Graph", icon: Share2 },
  {
    view: "refactoring",
    title: "Refactoring",
    icon: Wrench,
    count: (s) =>
      s?.counts.refactoringOpportunities != null
        ? `${s.counts.refactoringOpportunities.toLocaleString()} opportunit${s.counts.refactoringOpportunities === 1 ? "y" : "ies"}`
        : null,
  },
  {
    view: "decisions",
    title: "Decisions",
    icon: Scale,
    count: (s) =>
      s?.counts.decisions != null
        ? `${s.counts.decisions.toLocaleString()} decision records`
        : null,
  },
  { view: "docs", title: "Docs", icon: BookOpen },
  { view: "risk", title: "Review", icon: GitBranch },
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
        // The launcher stays useful without stats; the list drops its counts
        // and the health block shows its empty state.
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
    <div className="flex min-h-screen flex-col gap-4 bg-[var(--color-bg-root)] p-3">
      <RepoLine repoName={repo.name} branch={summary?.freshness.branch ?? null} />
      <Health health={summary?.health ?? null} loading={loading && !summary} />
      <Freshness
        freshness={summary?.freshness ?? null}
        updating={updating}
        onUpdate={runUpdate}
      />
      <NextSteps
        summary={summary}
        defaultBranch={repo.defaultBranch}
        onOpen={(view) => host.openView(view)}
      />
      <nav aria-label="Dashboards" className="flex flex-col">
        <p className={`${MICRO_LABEL} px-1 pb-1.5`}>Dashboards</p>
        <ul className="flex flex-col">
          {DESTINATIONS.map((d) => (
            <li key={d.view}>
              <DestinationRow
                icon={d.icon}
                title={d.title}
                count={d.count?.(summary) ?? null}
                onOpen={() => host.openView(d.view)}
              />
            </li>
          ))}
        </ul>
      </nav>
      <Footer host={host} />
    </div>
  );
}

function RepoLine({ repoName, branch }: { repoName: string; branch: string | null }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-2 px-1">
      <p
        className="min-w-0 truncate text-xs font-semibold text-[var(--color-text-primary)]"
        title={repoName}
      >
        {repoName}
      </p>
      {branch ? (
        <span className="flex min-w-0 shrink items-center gap-1 text-xs text-[var(--color-text-tertiary)]">
          <GitBranch className="h-3 w-3 shrink-0" aria-hidden />
          <span className="truncate font-mono" title={branch}>
            {branch}
          </span>
        </span>
      ) : null}
    </div>
  );
}

/**
 * The repo's health as a two-figure hairline ribbon: average health leads and
 * hotspot health (NLOC-weighted, more volatile) sits beside it. Each figure
 * takes its colour and word from the shared five-band reading, so this and the
 * dashboard it opens never disagree about one number. Quiet or small repos
 * gate hotspot scoring off, in which case only the average shows.
 */
function Health({ health, loading }: { health: HomeSummary["health"]; loading: boolean }) {
  if (loading) {
    return (
      <div className="space-y-2 px-1" role="status" aria-label="Loading health score">
        <div className="h-3 w-24 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        <div className="h-6 w-32 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        <div className="h-3 w-40 animate-pulse rounded bg-[var(--color-bg-inset)]" />
      </div>
    );
  }
  const headline = health?.average ?? health?.hotspot ?? null;
  if (!health || headline == null) {
    return (
      <EmptyState
        size="compact"
        title="No health data yet"
        description="Scores appear after the first full index."
      />
    );
  }
  const hasHotspot = health.average != null && health.hotspot != null;
  return (
    <section aria-label="Repository health" className="px-1">
      <dl className="grid grid-cols-2 gap-x-4">
        <Figure
          label={health.average != null ? "Average health" : "Hotspot health"}
          score={headline}
        />
        {hasHotspot && health.hotspot != null ? (
          <Figure label="Hotspot health" score={health.hotspot} delta={health.hotspotDelta} />
        ) : null}
      </dl>
      <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
        <span className="tabular-nums">{health.fileCount.toLocaleString()}</span> files scored
        {health.openFindings > 0 ? (
          <>
            {", "}
            <span className="tabular-nums">{health.openFindings.toLocaleString()}</span> open
            finding{health.openFindings === 1 ? "" : "s"}
          </>
        ) : null}
        .
      </p>
      {hasHotspot && health.history.length >= 2 ? <Sparkline values={health.history} /> : null}
    </section>
  );
}

function Figure({
  label,
  score,
  delta,
}: {
  label: string;
  score: number;
  delta?: number | null;
}) {
  const mark = healthMark(score);
  return (
    <div className="min-w-0">
      <dt className={MICRO_LABEL}>{label}</dt>
      <dd className="mt-1.5 flex flex-wrap items-baseline gap-x-1.5">
        <span
          className="text-[22px] font-semibold leading-none tracking-tight tabular-nums"
          style={{ color: mark.color }}
        >
          {formatScore(score)}
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">/ 10</span>
        <span className="text-xs text-[var(--color-text-secondary)]">{mark.label}</span>
        <DeltaMark delta={delta ?? null} />
      </dd>
    </div>
  );
}

/**
 * Hotspot movement since the last snapshot.
 *
 * Not coloured. Green beside a green health numeral is the two-marks-one-colour
 * trap: on the numeral green means "band: healthy", here it would mean
 * "direction: improved". Health owns green/amber/red on this surface because
 * those carry a band; direction is carried by the arrow, which needs no hue.
 */
function DeltaMark({ delta }: { delta: number | null }) {
  if (delta == null || delta === 0) return null;
  return (
    <span
      className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]"
      title="Hotspot health change since the previous snapshot"
    >
      {delta > 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}
    </span>
  );
}

/** Hotspot trend in neutral ink: the figures above already carry the band. */
function Sparkline({ values }: { values: number[] }) {
  const width = 100;
  const height = 20;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map(
      (v, i) =>
        `${(i * step).toFixed(1)},${(height - 3 - ((v - min) / span) * (height - 6)).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="mt-2 h-5 w-full text-[var(--color-text-tertiary)]"
      role="img"
      aria-label={`Hotspot health over the last ${values.length} snapshots`}
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.25" />
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

  let detail = "";
  if (stale && indexed && live) detail = `${indexed} → ${live}`;
  else if (indexed) {
    const ago = freshness?.lastIndexedAt ? timeAgo(freshness.lastIndexedAt) : "";
    detail = ago ? `${indexed} · indexed ${ago}` : indexed;
  }

  // The dot renders only when the index is behind: a marker every state
  // carries says nothing. The button is the row's one action, ink when the
  // index is behind because then it is the thing to do, an outline otherwise.
  return (
    <section
      aria-label="Index freshness"
      className="flex items-center gap-2.5 border-t border-[var(--color-border-default)] px-1 pt-3"
    >
      {stale ? (
        <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-warning)]" aria-hidden />
      ) : null}
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
      <Button
        size="sm"
        variant={stale && !updating ? "default" : "outline"}
        onClick={onUpdate}
        disabled={updating}
        title="Update the index with recent changes"
        className="shrink-0 gap-1.5 px-2.5 [&_svg]:size-3"
      >
        <RefreshCw className={updating ? "animate-spin" : undefined} />
        {updating ? "Updating" : "Update"}
      </Button>
    </section>
  );
}

/**
 * What to look at next, each row phrased as the question its dashboard
 * answers. Two rows at most; the full list follows.
 */
function NextSteps({
  summary,
  defaultBranch,
  onOpen,
}: {
  summary: HomeSummary | null;
  defaultBranch: string | null;
  onOpen: (view: PanelViewId) => void;
}) {
  // The Review panel takes its comparison base from the change list, so this
  // row names none.
  const branch = summary?.freshness.branch ?? null;
  const onDefault = branch == null || branch === (defaultBranch ?? "main");
  const findings = summary?.health?.openFindings ?? 0;
  const steps: { view: PanelViewId; title: string; detail: string }[] = [
    {
      view: "risk",
      title: onDefault ? "Check your change before you push" : "Check this branch before you push",
      detail: "Its risk, what it touches, and who could review it",
    },
  ];
  if (findings > 0) {
    steps.push({
      view: "health",
      title: "Review open findings",
      detail: `${findings.toLocaleString()} across the files scored`,
    });
  }
  return (
    <section
      aria-label="Next"
      className="flex flex-col border-t border-[var(--color-border-default)] pt-3"
    >
      <p className={`${MICRO_LABEL} px-1 pb-1.5`}>Next</p>
      {steps.map((step) => (
        <button
          key={step.title}
          type="button"
          onClick={() => onOpen(step.view)}
          className="flex w-full items-center gap-2 rounded-md px-1 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-wash-hover)]"
        >
          <span className="min-w-0 flex-1">
            <span className="block text-xs font-medium text-[var(--color-text-primary)]">
              {step.title}
            </span>
            <span className="block truncate text-xs text-[var(--color-text-tertiary)]">
              {step.detail}
            </span>
          </span>
          <ChevronRight
            className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]"
            aria-hidden
          />
        </button>
      ))}
    </section>
  );
}

function DestinationRow({
  icon: Icon,
  title,
  count,
  onOpen,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  count: string | null;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex h-8 w-full items-center gap-2.5 rounded-md px-1 text-left transition-colors hover:bg-[var(--color-bg-wash-hover)]"
    >
      <Icon className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)] transition-colors group-hover:text-[var(--color-text-secondary)]" />
      <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-primary)]">
        {title}
      </span>
      {count ? (
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
          {count}
        </span>
      ) : null}
    </button>
  );
}

/** Settings, the theme switcher and the brand mark. The preference applies to
 *  every Repowise webview and persists via the host; "auto" follows the editor. */
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

  return (
    <footer className="mt-auto flex flex-col gap-3 border-t border-[var(--color-border-default)] px-1 pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => host.openView("settings")}
          className="-ml-2 gap-1.5 px-2 [&_svg]:size-3.5"
        >
          <Settings2 />
          Settings
        </Button>
        <Segmented<ThemePreference>
          label="Theme"
          value={pref}
          onChange={choose}
          options={[
            { value: "auto", label: "Auto", hint: "Follow editor theme" },
            { value: "light", label: "Light", hint: "Light theme" },
            { value: "dark", label: "Dark", hint: "Dark theme" },
          ]}
        />
      </div>
      {/* The wordmark links home. A real anchor so it reads as a link to
          assistive tech; the click goes through the host, since a webview
          cannot navigate to an outside page itself. */}
      <a
        href={SITE_URL}
        title="repowise.dev"
        onClick={(event) => {
          event.preventDefault();
          host.openExternal(SITE_URL);
        }}
        className="flex w-fit items-center gap-1.5 rounded-sm"
      >
        <img
          src={kind === "dark" ? owlDarkTheme : owlLightTheme}
          alt=""
          className="h-3.5 w-3.5"
        />
        <span className="text-[10px] font-semibold tracking-wide text-[var(--color-text-tertiary)]">
          Repowise
        </span>
      </a>
    </footer>
  );
}

import { useCallback, useEffect, useState } from "react";
import {
  Copy,
  Gauge,
  GitCompare,
  GitPullRequest,
  Network,
  RotateCw,
  ShieldCheck,
  TestTube,
  Users,
} from "lucide-react";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui";
import { EmptyState } from "@repowise-dev/ui/shared";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";
import type {
  ChangeImpactReport,
  RiskRangeReport,
} from "../../../../src/shared/webviewMessages";
import { selectMissingCochanges } from "../../../../src/shared/changeImpact";
import { selectDirectRisks, type RankedDirectRisk } from "./selectors";

/** Human labels for the raw change features the endpoint returns, in report order. */
const FEATURE_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["la", "Lines added"],
  ["ld", "Lines deleted"],
  ["nf", "Files changed"],
  ["nd", "Directories changed"],
  ["ns", "Subsystems changed"],
  ["entropy", "Change entropy"],
  ["exp", "Author experience"],
];

type Tone = "low" | "medium" | "high";

/** Buckets a 0-10 score into the three risk tones the palette defines. */
function scoreTone(score: number): Tone {
  if (score >= 6.5) return "high";
  if (score >= 3.5) return "medium";
  return "low";
}

const TONE_VAR: Record<Tone, string> = {
  low: "var(--color-risk-low)",
  medium: "var(--color-risk-medium)",
  high: "var(--color-risk-high)",
};

/** Fallback co-change floor when settings cannot be read; mirrors the
 *  changeIntel.cochangeMinScore default. */
const COCHANGE_MIN_SCORE = 4;

/** Anchors the verdict chips scroll to; each id sits on its impact card. */
const SECTION_IDS = {
  directRisks: "impact-direct-risks",
  downstream: "impact-downstream",
  cochanges: "impact-cochanges",
  testGaps: "impact-test-gaps",
} as const;

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Formats a contribution with an explicit sign (positive raises risk). */
function signed(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(2)}`;
}

function formatFeatureValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function App({ host, repo, refreshToken }: ViewProps<"risk">) {
  const [report, setReport] = useState<RiskRangeReport | null>(null);
  const [impact, setImpact] = useState<ChangeImpactReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [impactLoading, setImpactLoading] = useState(true);
  const [cochangeFloor, setCochangeFloor] = useState(COCHANGE_MIN_SCORE);

  const load = useCallback(() => {
    setLoading(true);
    setImpactLoading(true);
    setError(null);
    // Keep the panel's co-change floor in step with the nudge's setting.
    host.api
      .getSettings()
      .then((s) => {
        const floor = s["changeIntel.cochangeMinScore"];
        if (typeof floor === "number") setCochangeFloor(floor);
      })
      .catch(() => undefined);
    host.api
      .riskRange()
      .then((r) => setReport(r))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Could not score change risk."),
      )
      .finally(() => setLoading(false));
    // Impact is independent: a git-less workspace still shows the risk score.
    host.api
      .changeImpact()
      .then((r) => setImpact(r))
      .catch(() => setImpact(null))
      .finally(() => setImpactLoading(false));
  }, [host]);

  // Refetch on mount and whenever the index moves under the panel. Both scopes
  // reflect the working tree, so there is no cache to reuse here.
  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const branch = report?.branch ?? repo.defaultBranch ?? "HEAD";
  const base = report?.base ?? "";

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">Change risk</h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <GitCompare className="h-4 w-4 shrink-0" />
            <span className="truncate">
              <code className="text-[var(--color-text-primary)]">{branch}</code>
              <span className="mx-1.5 text-[var(--color-text-tertiary)]">vs</span>
              <code className="text-[var(--color-text-primary)]">{base || "base"}</code>
            </span>
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RotateCw className={loading ? "animate-spin" : undefined} />
          Run again
        </Button>
      </header>

      {loading && !report ? (
        <RiskSkeleton base={base} />
      ) : error ? (
        <EmptyState
          icon={<ShieldCheck className="h-8 w-8" />}
          title="Could not score change risk"
          description={error}
          action={{ label: "Try again", onClick: load }}
        />
      ) : report ? (
        <>
          <ScoreHero report={report} />
          <VerdictStrip impact={impact} cochangeFloor={cochangeFloor} />
          <ChangeImpact
            impact={impact}
            loading={impactLoading}
            host={host}
            cochangeFloor={cochangeFloor}
          />
          <RiskBreakdown report={report} />
        </>
      ) : null}
    </div>
  );
}

/** Skeleton matching the report layout (score hero + driver bars) so the panel
 *  holds its shape while the working tree is scored. */
function RiskSkeleton({ base }: { base: string }) {
  return (
    <div className="space-y-5">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-6 py-6" aria-hidden>
          <div className="h-24 w-24 shrink-0 animate-pulse rounded-2xl bg-[var(--color-bg-inset)]" />
          <div className="min-w-0 flex-1 space-y-3">
            <div className="h-5 w-24 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            <div className="h-4 w-48 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            <div className="flex gap-2 pt-1">
              <div className="h-5 w-20 animate-pulse rounded bg-[var(--color-bg-inset)]" />
              <div className="h-5 w-28 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            </div>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">What moves the score</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3" aria-hidden>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <div className="h-3 w-40 shrink-0 animate-pulse rounded bg-[var(--color-bg-inset)]" />
              <div className="h-2 flex-1 animate-pulse rounded-full bg-[var(--color-bg-inset)]" />
              <div className="h-3 w-14 shrink-0 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            </div>
          ))}
        </CardContent>
      </Card>
      <p className="flex items-center justify-center gap-2 text-xs text-[var(--color-text-tertiary)]">
        <RotateCw className="h-3.5 w-3.5 animate-spin" />
        Scoring the working tree{base ? ` against ${base}` : ""}
      </p>
    </div>
  );
}

/** The Kamei diff-shape risk score for the committed range (base..HEAD). */
function ScoreHero({ report }: { report: RiskRangeReport }) {
  const r = report.result;
  const tone = scoreTone(r.score);
  const color = TONE_VAR[tone];
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-6 py-6">
        <div
          className="flex h-24 w-24 shrink-0 flex-col items-center justify-center rounded-2xl border"
          style={{
            borderColor: `color-mix(in srgb, ${color} 45%, transparent)`,
            backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)`,
          }}
        >
          <span className="text-3xl font-bold leading-none" style={{ color }}>
            {r.score.toFixed(1)}
          </span>
          <span className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">out of 10</span>
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium capitalize"
              style={{ color, borderColor: `color-mix(in srgb, ${color} 40%, transparent)` }}
            >
              {r.level} risk
            </span>
            {r.is_fix && (
              <Badge variant="outline" title="Classified as a fix change">
                fix
              </Badge>
            )}
          </div>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Estimated defect probability{" "}
            <span className="font-semibold text-[var(--color-text-primary)]">
              {(r.probability * 100).toFixed(1)}%
            </span>
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            {r.risk_percentile != null && (
              <Badge variant="default">{r.risk_percentile.toFixed(0)}th percentile</Badge>
            )}
            {r.review_priority && (
              <Badge variant="accent" className="capitalize">
                {r.review_priority} review priority
              </Badge>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/** Quiet one-line summary of the blast data. Each chip anchors its section;
 *  zero counts render no chip, and a clean tree renders nothing at all. */
function VerdictStrip({
  impact,
  cochangeFloor,
}: {
  impact: ChangeImpactReport | null;
  cochangeFloor: number;
}) {
  const blast = impact?.blast;
  if (!impact || !blast || impact.gitUnavailable) return null;

  const downstream = blast.transitive_affected.length;
  const cochanges = selectMissingCochanges(impact, cochangeFloor).length;
  const gaps = blast.test_gaps.length;

  const chips: Array<{ id: string; label: string }> = [];
  if (downstream > 0) {
    chips.push({
      id: SECTION_IDS.downstream,
      label: `may affect ${downstream} downstream file${downstream === 1 ? "" : "s"}`,
    });
  }
  if (cochanges > 0) {
    chips.push({
      id: SECTION_IDS.cochanges,
      label: `${cochanges} co-change partner${cochanges === 1 ? "" : "s"} untouched`,
    });
  }
  if (gaps > 0) {
    chips.push({
      id: SECTION_IDS.testGaps,
      label:
        gaps === 1
          ? "1 changed file has no associated test"
          : `${gaps} changed files have no associated test`,
    });
  }
  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {chips.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => scrollToSection(c.id)}
          className="inline-flex items-center rounded-full border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-surface)]"
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

/** The statistical breakdown: what moves the score, and the raw change shape. */
function RiskBreakdown({ report }: { report: RiskRangeReport }) {
  const r = report.result;
  const drivers = [...r.drivers].sort(
    (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
  );
  const maxDriver = drivers.reduce((m, d) => Math.max(m, Math.abs(d.contribution)), 0);
  const featureRows = FEATURE_LABELS.filter(
    ([key]) => r.features[key] != null,
  ) as ReadonlyArray<readonly [string, string]>;

  return (
    <>
      {drivers.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">What moves the score</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {drivers.map((d) => {
              const raises = d.contribution >= 0;
              const barColor = raises ? "var(--color-error)" : "var(--color-success)";
              const pct = maxDriver > 0 ? (Math.abs(d.contribution) / maxDriver) * 100 : 0;
              return (
                <div key={d.feature} className="flex items-center gap-3 text-sm">
                  <span className="w-40 shrink-0 truncate text-[var(--color-text-secondary)]">
                    {d.label}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${pct}%`, backgroundColor: barColor }}
                    />
                  </div>
                  <span
                    className="w-14 shrink-0 text-right font-medium tabular-nums"
                    style={{ color: barColor }}
                  >
                    {signed(d.contribution)}
                  </span>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {featureRows.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Change shape</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
              {featureRows.map(([key, label]) => (
                <div
                  key={key}
                  className="flex items-baseline justify-between gap-3 border-b border-[var(--color-border-default)] py-1.5 last:border-b-0"
                >
                  <dt className="text-sm text-[var(--color-text-secondary)]">{label}</dt>
                  <dd className="text-sm font-medium tabular-nums text-[var(--color-text-primary)]">
                    {formatFeatureValue(r.features[key] as number)}
                  </dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Change impact (blast radius + reviewers for the working change set)
// ---------------------------------------------------------------------------

function ChangeImpact({
  impact,
  loading,
  host,
  cochangeFloor,
}: {
  impact: ChangeImpactReport | null;
  loading: boolean;
  host: WebviewHost;
  cochangeFloor: number;
}) {
  if (loading && !impact) {
    return (
      <>
        <Card>
          <CardContent className="space-y-3 py-5" aria-hidden>
            <div className="h-4 w-40 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="h-3 flex-1 animate-pulse rounded bg-[var(--color-bg-inset)]" />
                <div className="h-1.5 w-16 shrink-0 animate-pulse rounded-full bg-[var(--color-bg-inset)]" />
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3 py-5" aria-hidden>
            <div className="h-4 w-40 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            <div className="h-3 w-full animate-pulse rounded bg-[var(--color-bg-inset)]" />
            <div className="h-3 w-2/3 animate-pulse rounded bg-[var(--color-bg-inset)]" />
          </CardContent>
        </Card>
      </>
    );
  }
  if (!impact) return null;

  if (impact.gitUnavailable) {
    return (
      <Card>
        <CardContent className="py-4 text-sm text-[var(--color-text-tertiary)]">
          Enable Git for this workspace to see what your change touches, who
          usually changes it with you, and who could review it.
        </CardContent>
      </Card>
    );
  }

  if (impact.changed.length === 0) {
    return (
      <EmptyState
        icon={<Network className="h-8 w-8" />}
        title="No pending changes"
        description="There are no uncommitted or unpushed changes to analyze. Impact appears here as soon as you edit or commit."
      />
    );
  }

  const blast = impact.blast;
  const directRisks = selectDirectRisks(impact);
  const downstream = blast?.transitive_affected ?? [];
  const cochanges = selectMissingCochanges(impact, cochangeFloor);
  const testGaps = blast?.test_gaps ?? [];
  const reviewers = impact.reviewers;
  const overall = blast?.overall_risk_score ?? null;

  const scopeLabel =
    impact.scope === "branch" ? "uncommitted and unpushed" : "uncommitted";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
          What this change touches
        </h2>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {impact.changed.length} {scopeLabel} file{impact.changed.length === 1 ? "" : "s"}
          {overall != null && (
            <>
              {" · "}
              <span className="font-medium text-[var(--color-text-secondary)]">
                impact {overall.toFixed(1)}/10
              </span>
            </>
          )}
        </span>
      </div>

      {directRisks.length > 0 && (
        <ImpactCard
          id={SECTION_IDS.directRisks}
          icon={<Gauge className="h-4 w-4" />}
          title="Riskiest files in this change"
          hint="Per-file risk from history and structure. Start your review here."
        >
          {directRisks.slice(0, 10).map((f) => (
            <DirectRiskRow key={f.path} risk={f} onOpen={() => host.openFile(f.path)} />
          ))}
          <MoreRow count={directRisks.length - 10} />
        </ImpactCard>
      )}

      {downstream.length > 0 && (
        <ImpactCard
          id={SECTION_IDS.downstream}
          icon={<Network className="h-4 w-4" />}
          title="Downstream of your changes"
          hint="These files depend on what you changed. Verify they still work."
        >
          {downstream.slice(0, 10).map((t) => (
            <PathRow
              key={t.path}
              path={t.path}
              trailing={`depth ${t.depth}`}
              onOpen={() => host.openFile(t.path)}
            />
          ))}
          <MoreRow count={downstream.length - 10} />
        </ImpactCard>
      )}

      {cochanges.length > 0 && (
        <ImpactCard
          id={SECTION_IDS.cochanges}
          icon={<GitPullRequest className="h-4 w-4" />}
          title="Usually changes together"
          hint="History suggests these often change with your edits. Advisory, not a rule."
        >
          {cochanges.slice(0, 8).map((c) => (
            <PathRow
              key={c.partner}
              path={c.partner}
              trailing={`${c.score}×`}
              onOpen={() => host.openFile(c.partner)}
            />
          ))}
          <MoreRow count={cochanges.length - 8} />
        </ImpactCard>
      )}

      {testGaps.length > 0 && (
        <ImpactCard
          id={SECTION_IDS.testGaps}
          icon={<TestTube className="h-4 w-4" />}
          title="Changed without a test"
          hint="These changed files have no associated test file."
        >
          {testGaps.slice(0, 8).map((p) => (
            <PathRow key={p} path={p} onOpen={() => host.openFile(p)} />
          ))}
          <MoreRow count={testGaps.length - 8} />
        </ImpactCard>
      )}

      {reviewers.length > 0 && <Reviewers reviewers={reviewers} host={host} />}
    </div>
  );
}

function ImpactCard({
  id,
  icon,
  title,
  hint,
  children,
}: {
  /** Anchor for the verdict chips; omitted for cards without a chip. */
  id?: string;
  icon: React.ReactNode;
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <Card id={id}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <span className="text-[var(--color-text-tertiary)]">{icon}</span>
          {title}
        </CardTitle>
        <p className="text-xs text-[var(--color-text-tertiary)]">{hint}</p>
      </CardHeader>
      <CardContent className="space-y-0.5">{children}</CardContent>
    </Card>
  );
}

function PathRow({
  path,
  trailing,
  onOpen,
}: {
  path: string;
  trailing?: string;
  onOpen: () => void;
}) {
  const name = path.split("/").pop() || path;
  const dir = path.slice(0, path.length - name.length);
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`Open ${path}`}
      className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-sm transition-colors hover:bg-[var(--color-bg-surface)]"
    >
      <span className="min-w-0 flex-1 truncate">
        {dir && <span className="text-[var(--color-text-tertiary)]">{dir}</span>}
        <span className="text-[var(--color-text-primary)]">{name}</span>
      </span>
      {trailing && (
        <span className="shrink-0 text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {trailing}
        </span>
      )}
    </button>
  );
}

/** A clickable file row with a quiet relative risk bar and a hotspot marker. */
function DirectRiskRow({ risk, onOpen }: { risk: RankedDirectRisk; onOpen: () => void }) {
  const name = risk.path.split("/").pop() || risk.path;
  const dir = risk.path.slice(0, risk.path.length - name.length);
  const pct = Math.min(100, Math.max(0, risk.share * 100));
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`Open ${risk.path}`}
      className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-sm transition-colors hover:bg-[var(--color-bg-surface)]"
    >
      <span className="min-w-0 flex-1 truncate">
        {dir && <span className="text-[var(--color-text-tertiary)]">{dir}</span>}
        <span className="text-[var(--color-text-primary)]">{name}</span>
      </span>
      {risk.hotspot && (
        <span
          className="shrink-0 text-xs text-[var(--color-text-tertiary)]"
          title="Changes often in recent history"
        >
          hotspot
        </span>
      )}
      <span
        className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]"
        title="Risk relative to the riskiest file in this change"
      >
        <span
          className="block h-full rounded-full"
          style={{
            width: `${pct}%`,
            backgroundColor:
              "color-mix(in srgb, var(--color-text-secondary) 45%, transparent)",
          }}
        />
      </span>
    </button>
  );
}

function MoreRow({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <p className="px-1.5 pt-1 text-xs text-[var(--color-text-tertiary)]">+{count} more</p>
  );
}

function Reviewers({
  reviewers,
  host,
}: {
  reviewers: ChangeImpactReport["reviewers"];
  host: WebviewHost;
}) {
  const top = reviewers.slice(0, 5);
  const copy = () => {
    const text = top
      .map((r) => (r.email ? `${r.name} <${r.email}>` : r.name))
      .join(", ");
    host.copyText(`Suggested reviewers: ${text}`, "Reviewers copied to clipboard.");
  };
  const maxScore = top.reduce((m, r) => Math.max(m, r.score), 0);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm">
            <span className="text-[var(--color-text-tertiary)]">
              <Users className="h-4 w-4" />
            </span>
            Suggested reviewers
          </CardTitle>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Ranked by ownership and co-change history of the changed files.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={copy}>
          <Copy className="h-3.5 w-3.5" />
          Copy
        </Button>
      </CardHeader>
      <CardContent className="space-y-2.5">
        {top.map((r) => {
          const pct = maxScore > 0 ? (r.score / maxScore) * 100 : 0;
          return (
            <div key={r.email ?? r.name} className="space-y-1">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 truncate font-medium text-[var(--color-text-primary)]">
                  {r.name}
                </span>
                <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
                  {r.recent_commits} recent commit{r.recent_commits === 1 ? "" : "s"}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
                <div
                  className="h-full rounded-full bg-[var(--color-accent-primary)]"
                  style={{ width: `${pct}%` }}
                />
              </div>
              {r.reasons.length > 0 && (
                <p className="truncate text-xs text-[var(--color-text-tertiary)]">
                  {r.reasons.join(" · ")}
                </p>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

import { useCallback, useEffect, useState } from "react";
import { GitCompare, RotateCw, ShieldCheck } from "lucide-react";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui";
import { EmptyState } from "@repowise-dev/ui/shared";
import type { ViewProps } from "../../runtime/mount";
import type { RiskRangeReport } from "../../../../src/shared/webviewMessages";

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

/** Formats a contribution with an explicit sign (positive raises risk). */
function signed(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(2)}`;
}

function formatFeatureValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function App({ host, repo, refreshToken }: ViewProps<"risk">) {
  const [report, setReport] = useState<RiskRangeReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    host.api
      .riskRange()
      .then((r) => {
        setReport(r);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Could not score branch risk.");
        setLoading(false);
      });
  }, [host]);

  // Refetch on mount and whenever the index moves under the panel. Risk
  // reflects the working HEAD, so there is no cache to reuse.
  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const branch = report?.branch ?? repo.defaultBranch ?? "HEAD";
  const base = report?.base ?? "";

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">Branch risk</h1>
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
        <LoadingCard base={base} />
      ) : error ? (
        <EmptyState
          icon={<ShieldCheck className="h-8 w-8" />}
          title="Could not score branch risk"
          description={error}
          action={{ label: "Try again", onClick: load }}
        />
      ) : report ? (
        <Report report={report} />
      ) : null}
    </div>
  );
}

function LoadingCard({ base }: { base: string }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
        <RotateCw className="h-6 w-6 animate-spin text-[var(--color-accent-primary)]" />
        <p className="text-sm text-[var(--color-text-secondary)]">
          Scoring the working tree{base ? ` against ${base}` : ""}
        </p>
      </CardContent>
    </Card>
  );
}

function Report({ report }: { report: RiskRangeReport }) {
  const r = report.result;
  const tone = scoreTone(r.score);
  const color = TONE_VAR[tone];

  const drivers = [...r.drivers].sort(
    (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
  );
  const maxDriver = drivers.reduce((m, d) => Math.max(m, Math.abs(d.contribution)), 0);

  const featureRows = FEATURE_LABELS.filter(
    ([key]) => r.features[key] != null,
  ) as ReadonlyArray<readonly [string, string]>;

  return (
    <div className="space-y-5">
      {/* Score hero */}
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
                style={{
                  color,
                  borderColor: `color-mix(in srgb, ${color} 40%, transparent)`,
                }}
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
                <Badge variant="default">
                  {r.risk_percentile.toFixed(0)}th percentile
                </Badge>
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

      {/* Drivers */}
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

      {/* Change features */}
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
    </div>
  );
}

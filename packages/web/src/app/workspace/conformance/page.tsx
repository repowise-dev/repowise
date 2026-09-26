import type { Metadata } from "next";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import type {
  ConformanceReport,
  ArchitectureMetrics,
} from "@repowise-dev/api-client/types";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede, LedeLink } from "@repowise-dev/ui/shared/page-lede";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { buildDsm, DsmMatrixView } from "@repowise-dev/ui/workspace/dsm";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import {
  getWorkspaceSystemGraph,
  getWorkspaceConformance,
  getWorkspaceArchitecture,
} from "@/lib/api/workspace";
import { ConformanceAiPrompt } from "./conformance-ai-prompt";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("conformance");
  return { title: t("title") };
}

export const revalidate = 30;

/**
 * Whether the conformance analyser has ever produced this report.
 *
 * A report with no `generated_at` was never written a result, and its zero
 * counts are the absence of a check rather than a clean pass. Three states
 * have to stay separate: never ran, ran with no rules to check, and ran
 * against real rules.
 *
 * `generated_at` is now `string | null`, and the checker stamps it whenever it
 * runs. Artifacts written before that carry `""`, which the loader maps to
 * null, so the falsy check covers both.
 */
function reportState(
  report: ConformanceReport | null,
): "unavailable" | "never_ran" | "no_rules" | "checked" {
  if (!report) return "unavailable";
  if (!report.generated_at) return "never_ran";
  if ((report.rules_evaluated ?? 0) === 0) return "no_rules";
  return "checked";
}

export default async function ConformancePage() {
  const t = await getTranslations("conformance");

  const [sg, cf, arch] = await Promise.allSettled([
    getWorkspaceSystemGraph(),
    getWorkspaceConformance(),
    getWorkspaceArchitecture(),
  ]);

  const graph = sg.status === "fulfilled" ? sg.value : null;
  const report = cf.status === "fulfilled" ? cf.value : null;
  const metrics = arch.status === "fulfilled" ? arch.value : null;

  const state = reportState(report);
  const violations = state === "checked" ? (report?.violations ?? []) : [];
  // Cycle *membership* comes from the report, but the count is recomputed from
  // the graph on every request, so it stays true even when nothing has run.
  const cycles = report?.cycles ?? [];
  const cycleCount = metrics?.cycle_count ?? cycles.length;

  const matrix = buildDsm(graph, report);

  const ribbon: RibbonStat[] = [
    {
      label: t("ribbon.services"),
      value: metrics ? formatNumber(metrics.node_count) : "—",
      sub: t("ribbon.servicesSub"),
    },
    {
      label: t("ribbon.structuralLinks"),
      value: metrics ? formatNumber(metrics.structural_edge_count) : "—",
      sub: t("ribbon.structuralLinksSub"),
    },
    {
      label: t("ribbon.propagationCost"),
      value: metrics ? `${metrics.propagation_cost_pct.toFixed(1)}%` : "—",
      sub: t("ribbon.propagationCostSub"),
    },
    {
      label: t("ribbon.coreSize"),
      value: metrics ? formatNumber(metrics.core_size) : "—",
      sub: metrics
        ? t("ribbon.coreSizeSubPct", { pct: Math.round(metrics.core_ratio * 100) })
        : t("ribbon.coreSizeSub"),
    },
    {
      label: t("ribbon.cycles"),
      value: metrics ? formatNumber(cycleCount) : "—",
      sub: t("ribbon.cyclesSub"),
    },
  ];

  return (
    <PageShell
      title={t("title")}
      icon={<ShieldCheck className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description={t("description")}
    >
      <PageLede
        label={t("ledeLabel")}
        value={metrics ? metrics.score.toFixed(1) : "—"}
        unit={t("ledeUnit")}
        {...(metrics?.architecture_type
          ? { band: { label: metrics.architecture_type } }
          : {})}
        layout="beside"
        action={
          <LedeLink href="/workspace/system-map" LinkComponent={Link}>
            {t("ledeSeeSystemMap")}
          </LedeLink>
        }
      >
        {metrics ? (
          <p>
            {t("ledeSentence", {
              pct: metrics.propagation_cost_pct.toFixed(1),
              links: formatNumber(metrics.structural_edge_count),
              services: formatNumber(metrics.node_count),
            })}
          </p>
        ) : (
          <p>{t("ledeNoGraph")}</p>
        )}
        <RuleSentence state={state} report={report} />
      </PageLede>

      <StatRibbon stats={ribbon} />

      {/*
        The matrix counts a co-change relationship as a filled cell and the
        ribbon's structural-link figure does not, so the two numbers differ by
        design and sit a few hundred pixels apart. Reconciled in
        `dsmDescription` rather than left for the reader to work out, and
        stated there rather than changing the shared matrix's own caption,
        which is accurate for what it counts.
      */}
      <OverviewSection
        title={t("dsmTitle")}
        description={t("dsmDescription")}
        action={
          <SectionLink href="/workspace/system-map" LinkComponent={Link}>
            {t("dsmSystemMapLink")}
          </SectionLink>
        }
      >
        <DsmMatrixView matrix={matrix} {...(metrics ? { metrics } : {})} />
      </OverviewSection>

      <OverviewSection
        title={
          state === "checked"
            ? t("violationsTitleCount", { count: violations.length })
            : t("violationsTitle")
        }
        description={t("violationsDescription")}
        {...(violations.length > 0
          ? { action: <ConformanceAiPrompt violations={violations} /> }
          : {})}
      >
        {violations.length === 0 ? (
          <ViolationsEmptyState state={state} />
        ) : (
          <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
            {violations.map((v) => (
              <li
                key={`${v.edge_id}:${v.rule_source}:${v.rule_target}`}
                className="py-3.5"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {v.source_name || v.source}
                  </span>
                  <span aria-hidden className="text-[var(--color-text-tertiary)]">
                    →
                  </span>
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {v.target_name || v.target}
                  </span>
                  <span className="font-mono text-[11px] text-[var(--color-text-tertiary)]">
                    {v.edge_kind}
                  </span>
                </div>
                <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                  {t("violationBreaks")}{" "}
                  <span className="font-mono text-[var(--color-warning)]">
                    {v.rule_source} !&gt; {v.rule_target}
                  </span>
                  {v.rule_description ? ` — ${v.rule_description}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </OverviewSection>

      <OverviewSection
        title={t("cyclesTitle", { count: cycleCount })}
        description={t("cyclesDescription")}
      >
        {cycles.length === 0 ? (
          <EmptyState
            className="p-6"
            title={
              cycleCount > 0
                ? t("cyclesDetectedNotListed", { count: cycleCount })
                : t("noCyclesTitle")
            }
            description={
              cycleCount > 0
                ? t("cyclesUnlistedDescription")
                : t("noCyclesDescription")
            }
          />
        ) : (
          <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
            {cycles.map((c) => (
              <li key={c.nodes.join("->")} className="py-3.5">
                <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                  {t("cycleServices", { count: c.length })}
                </p>
                <p className="mt-1 text-sm text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {c.nodes.join(" → ")} → {c.nodes[0]}
                </p>
              </li>
            ))}
          </ul>
        )}
      </OverviewSection>
    </PageShell>
  );
}

/** The sentence in the lede that says whether the check has anything to say. */
async function RuleSentence({
  state,
  report,
}: {
  state: ReturnType<typeof reportState>;
  report: ConformanceReport | null;
}) {
  const t = await getTranslations("conformance");
  if (state === "checked") {
    const n = report?.rules_evaluated ?? 0;
    const v = report?.violations?.length ?? 0;
    return (
      <p>
        {v === 0
          ? t("ruleCheckedClean", { rules: n })
          : t("ruleCheckedViolations", { rules: n, violations: v })}
      </p>
    );
  }
  if (state === "no_rules") {
    return (
      <p>
        {t.rich("ruleNoneDeclared", {
          c: (chunks) => (
            <code className="font-mono text-[var(--color-text-primary)]">
              {chunks}
            </code>
          ),
          f: (chunks) => (
            <code className="font-mono text-[var(--color-text-primary)]">
              {chunks}
            </code>
          ),
        })}
      </p>
    );
  }
  return (
    <p>
      {t.rich("ruleNeverRan", {
        c: (chunks) => (
          <code className="font-mono text-[var(--color-text-primary)]">
            {chunks}
          </code>
        ),
      })}
    </p>
  );
}

/**
 * Three empty states, because they mean three different things.
 *
 * Collapsing them into one "no violations" message is the failure this page
 * shipped with: the artifact's zeros are written before anything runs.
 */
async function ViolationsEmptyState({
  state,
}: {
  state: ReturnType<typeof reportState>;
}) {
  const t = await getTranslations("conformance");
  if (state === "checked") {
    return (
      <EmptyState
        className="p-6"
        title={t("emptyNoRulesBrokenTitle")}
        description={t("emptyNoRulesBrokenDescription")}
      />
    );
  }
  if (state === "no_rules") {
    return (
      <EmptyState
        className="p-6"
        title={t("emptyNoRulesDeclaredTitle")}
        description={t("emptyNoRulesDeclaredDescription")}
      />
    );
  }
  if (state === "never_ran") {
    return (
      <EmptyState
        className="p-6"
        title={t("emptyNotCheckedTitle")}
        description={t("emptyNotCheckedDescription")}
      />
    );
  }
  return (
    <EmptyState
      className="p-6"
      title={t("emptyUnavailableTitle")}
      description={t("emptyUnavailableDescription")}
    />
  );
}

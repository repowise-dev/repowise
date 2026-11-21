"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Segmented } from "@repowise-dev/ui/shared/segmented";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import { AGENT_PROMPT_LABEL, useAiPromptAction } from "@repowise-dev/ui/health/ai-prompt-action";
import { buildSetupAiPrompt } from "@repowise-dev/ui/health/ai-prompt-builder";
import {
  ACCOUNTING_METHOD_VERSION,
  RepowiseUsage,
  SavingsNudge,
  SavingsPageBody,
  SavingsResetNotice,
  type SpendGrouping,
} from "@repowise-dev/ui/savings";
import { getCostSummary, getSavings, listCosts } from "@/lib/api/costs";
import { getMcpToolSurface } from "@/lib/api/mcp-tools";
import type { SetupResponse } from "@/lib/api/setup";
import { useRepo } from "@/lib/hooks/use-repo";
import { useSetupData, useSetupView } from "@/components/setup/use-setup";
import { useSetupText } from "@/components/setup/setup-row";
import { setupHref } from "@/components/settings/settings-groups";
import type { SetupRow } from "@/components/setup/setup-rows";
import { useSavingsCopy } from "./savings-copy";
import { checkAgents, checkLinks, checksHrefs, connectedAgentIds } from "./savings-checks";
import {
  jobsNotSetUp,
  nudgeHref,
  pickSavingsNudge,
  readDismissed,
  rememberDismissed,
  type SavingsNudgeId,
} from "./savings-nudge";

/** Where the accounting and the benchmark are written down. */
const METHODOLOGY_HREF =
  "https://github.com/repowise-dev/repowise/blob/main/docs/architecture/savings-accounting.md";
const BENCHMARK_HREF = "https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md";

/** Dismissal is scoped to a repository *and* an accounting-method version, so
 *  a later methodology change announces itself once more. */
function resetNoticeKey(repoId: string): string {
  return `repowise:savings-reset-dismissed:${repoId}:v${ACCOUNTING_METHOD_VERSION}`;
}

type Range = "7" | "30" | "all";
const RANGE_DAYS: Record<Range, number | undefined> = { "7": 7, "30": 30, all: undefined };
// Previous data stays on screen while another range loads, so the body (and
// the chart's lens) does not unmount on every range change.
const QUIET = { revalidateOnFocus: false, keepPreviousData: true };

/**
 * Usage & savings: what agents avoided, what Repowise itself used, and how it
 * is counted. The body is the shared props-only `SavingsPageBody`; this page
 * owns fetching, the time range, the words and the one setup nudge.
 *
 * One fetch wave on load: savings, spend summary, spend rows, setup state and
 * the repo name, in parallel. Switching the spend grouping fetches only that
 * grouping. No accounting arithmetic here: the server aggregates and prices.
 */
export default function CostsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const t = useTranslations("views.costs");
  const copy = useSavingsCopy();

  const [range, setRange] = useState<Range>("all");
  const [grouping, setGrouping] = useState<SpendGrouping>("operation");
  const days = RANGE_DAYS[range];
  // Fixed per range so the SWR keys stay stable across renders.
  const since = useMemo(
    () => (days === undefined ? undefined : new Date(Date.now() - days * 86_400_000).toISOString()),
    [days],
  );

  const savings = useSWR(
    [`savings:${id}`, range],
    () => getSavings(id, days === undefined ? {} : { days }),
    QUIET,
  );
  const summary = useSWR([`costs-summary:${id}`, range], () => getCostSummary(id, since), QUIET);
  const rows = useSWR(
    [`costs:${id}`, grouping, range],
    () => listCosts(id, since ? { by: grouping, since } : { by: grouping }),
    // Never the previous grouping's rows under the new grouping's header.
    { revalidateOnFocus: false },
  );
  const { data: setup } = useSetupData(id);
  // Per-agent tool lists, once Setup says which agents are connected.
  const connected = connectedAgentIds(setup);
  const tools = useSWR(
    connected.length ? [`mcp-tools:${id}`, connected.join(",")] : null,
    () => getMcpToolSurface(id, connected),
    { revalidateOnFocus: false },
  );
  const { repo } = useRepo(id);
  const view = useSetupView(id, repo?.name ?? id, setup);
  const nudge = useSavingsNudge(id, setup, savings.data?.unique_events ?? 0, view?.optional ?? []);

  const usage = {
    summary: summary.data,
    rows: rows.data,
    grouping,
    onGroupingChange: setGrouping,
    fallback:
      summary.data === undefined || summary.error ? (
        <SpendFallback failed={Boolean(summary.error)} retry={summary.mutate} />
      ) : undefined,
  };
  const agentRow = view?.essentials.find((row) => row.id === "agent");

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      actions={
        <Segmented<Range>
          label={t("rangeLabel")}
          value={range}
          onChange={setRange}
          options={[
            { value: "7", label: t("range7") },
            { value: "30", label: t("range30") },
            { value: "all", label: t("rangeAll") },
          ]}
        />
      }
    >
      <ResetNotice repoId={id} />

      {savings.error ? (
        <div className="flex flex-col gap-14">
          <ApiError
            title={t("savingsErrorTitle")}
            message={summary.error ? t("savingsErrorBody") : t("savingsErrorBodySpendUnaffected")}
            onRetry={() => void savings.mutate()}
          />
          {!summary.error && <RepowiseUsage copy={copy} {...usage} />}
        </div>
      ) : savings.data === undefined ? (
        <SavingsSkeleton />
      ) : (
        <SavingsPageBody
          savings={savings.data}
          copy={copy}
          nudge={nudge.element}
          notSetUp={jobsNotSetUp(setup)}
          checks={{
            agents: checkAgents(setup, tools.data),
            profiles: tools.data?.profiles,
            links: checkLinks(id),
            ...checksHrefs(id),
          }}
          emptyActions={
            <>
              {agentRow && <AiPromptButton label={AGENT_PROMPT_LABEL} onClick={() => nudge.open(agentRow)} />}
              <Link href={setupHref(id)} className="text-xs text-[var(--color-accent-primary)] hover:underline">
                {t("openSetup")}
              </Link>
            </>
          }
          methodologyHref={METHODOLOGY_HREF}
          benchmarkHref={BENCHMARK_HREF}
          LinkComponent={Link}
          usage={usage}
        />
      )}
      {nudge.modal}
    </PageShell>
  );
}

/**
 * The one setup suggestion and the prompt modal. Gating is `pickSavingsNudge`;
 * this wires the words, the Setup row's own prompt and the dismissal.
 */
function useSavingsNudge(
  repoId: string,
  setup: SetupResponse | undefined,
  calls: number,
  rows: SetupRow[],
) {
  const t = useTranslations("views.costs");
  const tSetup = useTranslations("setup");
  const text = useSetupText();
  const { openPrompt, promptModal } = useAiPromptAction();
  const [dismissed, setDismissed] = useState<Set<SavingsNudgeId> | null>(null);

  // Read after mount so a dismissed suggestion never flashes back.
  useEffect(() => setDismissed(readDismissed(repoId)), [repoId]);

  const open = useCallback(
    (row: SetupRow) =>
      openPrompt({
        title: text(row.title),
        description: tSetup("promptDescription"),
        getPrompt: (flavor) => buildSetupAiPrompt(row.prompt, flavor),
      }),
    [openPrompt, text, tSetup],
  );

  const id = dismissed ? pickSavingsNudge(setup, calls, dismissed) : null;
  const row = id ? rows.find((r) => r.id === id) : undefined;
  const element = id ? (
    <SavingsNudge
      title={t(id === "rewrite" ? "nudgeRewriteTitle" : "nudgeModelTitle")}
      body={t(id === "rewrite" ? "nudgeRewriteBody" : "nudgeModelBody")}
      action={row ? <AiPromptButton label={AGENT_PROMPT_LABEL} onClick={() => open(row)} /> : null}
      href={nudgeHref(repoId, id)}
      linkLabel={t("openSetup")}
      dismissLabel={t("nudgeDismiss")}
      onDismiss={() => {
        rememberDismissed(repoId, id);
        setDismissed((prev) => new Set([...(prev ?? []), id]));
      }}
      LinkComponent={Link}
    />
  ) : null;

  return { element, open, modal: promptModal };
}

/** Spend while it loads, or after it failed. */
function SpendFallback({ failed, retry }: { failed: boolean; retry: () => unknown }) {
  const t = useTranslations("views.costs");
  if (failed) return <ApiError title={t("spendErrorTitle")} onRetry={() => void retry()} />;
  return (
    <SkeletonRegion label={t("loadingSpend")} className="flex flex-col gap-3">
      <Skeleton className="h-5 w-48" />
      <Skeleton className="h-14 w-full" />
    </SkeletonRegion>
  );
}

/** Reserves the hero and proof row while the report is in flight. */
function SavingsSkeleton() {
  const t = useTranslations("views.costs");
  return (
    <SkeletonRegion label={t("loadingSavings")} className="flex flex-col gap-8">
      <Skeleton className="h-[220px] w-full rounded-2xl" />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-2">
            <Skeleton className="h-2.5 w-24" />
            <Skeleton className="h-6 w-20" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

/** The savings-reset disclosure, once per repository and accounting version.
 *  Starts hidden and reveals after the storage read. */
function ResetNotice({ repoId }: { repoId: string }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      setVisible(!window.localStorage.getItem(resetNoticeKey(repoId)));
    } catch {
      // Storage unavailable: show it. Shown twice beats never explained.
      setVisible(true);
    }
  }, [repoId]);

  const dismiss = useCallback(() => {
    try {
      window.localStorage.setItem(resetNoticeKey(repoId), "1");
    } catch {
      // Not remembered; hidden for now.
    }
    setVisible(false);
  }, [repoId]);

  if (!visible) return null;
  return <SavingsResetNotice onDismiss={dismiss} methodologyHref={METHODOLOGY_HREF} LinkComponent={Link} />;
}

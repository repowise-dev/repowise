"use client";

/**
 * Refactoring — `/repos/[id]/refactoring`.
 *
 * A first-class surface for the deterministic refactoring plans the health
 * pass writes (Extract Class, Extract Helper, Move Method, Break Cycle, Split
 * File). The
 * card board is the primary view; a priority×effort quadrant heads it. Type
 * filters are URL-synced via `?type=` (nuqs), mirroring the architecture view.
 */

import { use, useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { Wrench, RotateCw } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { RefactoringBoard, TYPE_ORDER, typeMeta } from "@repowise-dev/ui/refactoring";
import type { RefactoringPlan, RefactoringTargets } from "@repowise-dev/ui/refactoring";
import { AiPromptModal, buildRefactoringPlanPrompt } from "@repowise-dev/ui/health";
import {
  generateRefactoringCode,
  getRefactoringSettings,
  getRefactoringTargets,
  type RefactoringSettings,
} from "@/lib/api/refactoring";

const TYPE_VALUES = ["all", ...TYPE_ORDER] as const;
type TypeFilter = (typeof TYPE_VALUES)[number];

export default function RefactoringPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: repoId } = use(params);
  const [type, setType] = useQueryState(
    "type",
    parseAsStringLiteral(TYPE_VALUES).withDefault("all"),
  );

  const { data, error, isLoading, mutate } = useSWR<RefactoringTargets>(
    `refactoring:${repoId}`,
    () => getRefactoringTargets(repoId),
    { revalidateOnFocus: false },
  );

  const filtered = useMemo(() => {
    const plans = data?.plans ?? [];
    return type === "all" ? plans : plans.filter((p) => p.refactoring_type === type);
  }, [data?.plans, type]);

  const prefix = `/repos/${repoId}`;
  const fileHref = (path: string) => fileEntityPath(prefix, path);

  // AI-prompt modal (flavor picker + copy) — same UX as the health surface.
  const [promptPlan, setPromptPlan] = useState<RefactoringPlan | null>(null);
  const onAiPrompt = useCallback((plan: RefactoringPlan) => setPromptPlan(plan), []);

  // Opt-in code generation. Enabled only when the repo's config turns it on
  // (a local-`serve` capability); the settings call 404s on hosted backends,
  // which simply leaves the action hidden.
  const { data: settings } = useSWR<RefactoringSettings>(
    `refactoring-settings:${repoId}`,
    () => getRefactoringSettings(repoId),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const onGenerateCode = useMemo(
    () =>
      settings?.enabled
        ? (plan: RefactoringPlan) => generateRefactoringCode(repoId, plan.id)
        : undefined,
    [settings?.enabled, repoId],
  );

  const counts = new Map((data?.summary.by_type ?? []).map((c) => [c.type, c.count]));
  const tabs = [
    { id: "all" as const, label: "All", badge: data?.summary.total },
    ...TYPE_ORDER.map((t) => ({
      id: t,
      label: typeMeta(t).label,
      badge: counts.get(t) ?? 0,
    })),
  ].filter((t) => t.id === "all" || (t.badge ?? 0) > 0);

  return (
    <PageShell
      title="Refactoring"
      icon={<Wrench className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      description="Concrete, ranked refactoring plans — split, dedupe, move, and decouple. Copy any plan straight to your coding agent."
      actions={
        <button
          type="button"
          onClick={() => void mutate()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"
        >
          <RotateCw className="h-3.5 w-3.5" />
          Refresh
        </button>
      }
    >
      <div className="space-y-6">
        <ViewTabs
          tabs={tabs}
          value={type}
          onValueChange={(id) => void setType(id as TypeFilter)}
        />

        {error ? (
          <div className="rounded-2xl border border-[var(--color-error)]/30 bg-[var(--color-error)]/5 p-6 text-sm text-[var(--color-text-secondary)]">
            Couldn&apos;t load refactoring plans. The repo may not be indexed yet, or the API is
            unreachable.
          </div>
        ) : isLoading ? (
          <div className="grid gap-5 xl:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-56 animate-pulse rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
              />
            ))}
          </div>
        ) : (
          <RefactoringBoard
            plans={filtered}
            summary={data?.summary}
            onAiPrompt={onAiPrompt}
            onGenerateCode={onGenerateCode}
            settingsHref={`${prefix}/settings`}
            fileHref={fileHref}
          />
        )}
      </div>

      <AiPromptModal
        open={promptPlan !== null}
        onOpenChange={(open) => {
          if (!open) setPromptPlan(null);
        }}
        getPrompt={
          promptPlan
            ? (flavor) => buildRefactoringPlanPrompt({ plan: promptPlan, flavor })
            : null
        }
        filePath={promptPlan?.file_path ?? null}
        title="AI refactoring prompt"
        description="A ready-to-paste plan that hands your AI coding agent the exact steps, the blast radius to keep consistent, and a completion contract."
      />
    </PageShell>
  );
}

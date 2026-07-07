"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Search, Wrench } from "lucide-react";
import { Input } from "../ui/input";
import { RefactoringPlanCard } from "./refactoring-plan-card";
import { RefactoringQuadrant } from "./refactoring-quadrant";
import { RefactoringModal } from "./refactoring-modal";
import { typeAccent, typeMeta, CONFIDENCE_LABEL, TYPE_ORDER } from "./meta";
import {
  blastCount,
  type Confidence,
  type EffortBucket,
  type GeneratedCode,
  type RefactoringPlan,
  type RefactoringSummary,
} from "./types";

const PAGE_SIZE = 60;

const EFFORTS: EffortBucket[] = ["S", "M", "L", "XL"];
const EFFORT_RANK: Record<EffortBucket, number> = { S: 0, M: 1, L: 2, XL: 3 };
const CONFIDENCES: Confidence[] = ["high", "medium", "low"];

type SortKey = "priority" | "health" | "effort" | "blast" | "file";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "priority", label: "Priority" },
  { value: "health", label: "Health recovered" },
  { value: "effort", label: "Effort (low first)" },
  { value: "blast", label: "Blast radius" },
  { value: "file", label: "File (A–Z)" },
];

export interface RefactoringBoardProps {
  plans: RefactoringPlan[];
  summary?: RefactoringSummary;
  /** Open the AI-prompt modal for a plan (host owns the modal + flavor). */
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  /** Opt-in LLM code generation for a plan (host owns the API call). Passed
   *  through to the inspector modal; omit to hide the action. */
  onGenerateCode?: ((plan: RefactoringPlan) => Promise<GeneratedCode>) | undefined;
  /** Link to repo settings (provider/model), shown beside the Generate action. */
  settingsHref?: string | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  /** Show the priority×effort quadrant centerpiece (default true). */
  showQuadrant?: boolean;
  emptyTitle?: string;
  emptyHint?: string;
}

/**
 * The Refactoring surface. The priority×effort quadrant is the full-width
 * centerpiece; a searchable, sortable, filterable grid of compact plan cards
 * sits below it. Clicking the quadrant or a card opens the visual modal for
 * that one plan. No table.
 */
export function RefactoringBoard({
  plans,
  summary,
  onAiPrompt,
  onGenerateCode,
  settingsHref,
  fileHref,
  showQuadrant = true,
  emptyTitle = "No refactoring plans",
  emptyHint = "Nothing crosses the precision bar for this repo yet. Plans appear here when a class is worth splitting, a clone worth extracting, a method worth moving, a cycle worth cutting, or a file worth decomposing.",
}: RefactoringBoardProps) {
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [selected, setSelected] = useState<RefactoringPlan | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  // List controls — make hundreds of plans browsable.
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("priority");
  const [effortSel, setEffortSel] = useState<Set<EffortBucket>>(new Set());
  const [confSel, setConfSel] = useState<Set<Confidence>>(new Set());

  // Render the top-ranked cards first and grow on demand — a repo can surface
  // hundreds of plans and mounting them all at once would jank the grid.
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const toggle = useCallback(<T,>(setter: React.Dispatch<React.SetStateAction<Set<T>>>, value: T) => {
    setter((cur) => {
      const next = new Set(cur);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  const processed = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = plans.filter((p) => {
      if (q && !`${p.file_path} ${p.target_symbol}`.toLowerCase().includes(q)) return false;
      if (effortSel.size && !effortSel.has((p.effort_bucket || "M") as EffortBucket)) return false;
      if (confSel.size && !confSel.has((p.confidence || "medium") as Confidence)) return false;
      return true;
    });
    // `priority` keeps the backend's rank order; the rest sort a copy.
    if (sortKey !== "priority") {
      out = [...out].sort((a, b) => {
        switch (sortKey) {
          case "health":
            return b.impact_delta - a.impact_delta;
          case "effort":
            return (
              EFFORT_RANK[(a.effort_bucket || "M") as EffortBucket] -
              EFFORT_RANK[(b.effort_bucket || "M") as EffortBucket]
            );
          case "blast":
            return blastCount(b) - blastCount(a);
          case "file":
            return a.file_path.localeCompare(b.file_path);
          default:
            return 0;
        }
      });
    }
    return out;
  }, [plans, query, sortKey, effortSel, confSel]);

  // Reset the window whenever the visible set changes (filter/sort/search/type).
  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [processed]);

  const openPlan = useCallback((plan: RefactoringPlan) => {
    setSelected(plan);
    setModalOpen(true);
  }, []);

  const pickFromQuadrant = useCallback(
    (plan: RefactoringPlan) => {
      setHighlighted(plan.id);
      openPlan(plan);
      window.setTimeout(() => setHighlighted((cur) => (cur === plan.id ? null : cur)), 2200);
    },
    [openPlan],
  );

  if (plans.length === 0) {
    return (
      <div className="mx-auto flex max-w-md flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-8 py-16 text-center">
        <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)]">
          <Wrench className="h-6 w-6" />
        </span>
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{emptyTitle}</h3>
        <p className="mt-1.5 text-sm leading-relaxed text-[var(--color-text-tertiary)]">{emptyHint}</p>
      </div>
    );
  }

  const counts = new Map((summary?.by_type ?? []).map((c) => [c.type, c.count]));
  const filtersActive = query.trim() !== "" || effortSel.size > 0 || confSel.size > 0;

  return (
    <div className="space-y-8">
      {showQuadrant ? (
        <section className="space-y-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
                Priority × effort
              </h2>
              <p className="mt-0.5 max-w-2xl text-sm text-[var(--color-text-secondary)]">
                Ranked by leverage — how depended-upon the file is, how much rides along, and the
                health recovered. The top-left is where to start.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {TYPE_ORDER.filter((t) => (counts.get(t) ?? 0) > 0).map((t) => {
                const meta = typeMeta(t);
                const { Icon } = meta;
                return (
                  <span
                    key={t}
                    className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)]"
                  >
                    <Icon className="h-3.5 w-3.5" style={{ color: typeAccent(t) }} />
                    {meta.label}
                    <span className="tabular-nums text-[var(--color-text-tertiary)]">{counts.get(t)}</span>
                  </span>
                );
              })}
            </div>
          </div>
          <RefactoringQuadrant plans={plans} onSelect={pickFromQuadrant} height={400} />
        </section>
      ) : null}

      <section className="space-y-4">
        {/* control bar */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search file or symbol…"
              className="pl-9"
              aria-label="Search plans"
            />
          </div>

          <div className="flex items-center gap-1.5">
            <label htmlFor="refactoring-sort" className="text-xs text-[var(--color-text-tertiary)]">
              Sort
            </label>
            <select
              id="refactoring-sort"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              className="h-9 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 text-sm text-[var(--color-text-primary)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)]"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* quick filters */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
              Effort
            </span>
            {EFFORTS.map((e) => (
              <FilterChip
                key={e}
                active={effortSel.has(e)}
                onClick={() => toggle(setEffortSel, e)}
                label={e}
              />
            ))}
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
              Confidence
            </span>
            {CONFIDENCES.map((c) => (
              <FilterChip
                key={c}
                active={confSel.has(c)}
                onClick={() => toggle(setConfSel, c)}
                label={CONFIDENCE_LABEL[c]}
              />
            ))}
          </div>
        </div>

        {/* count + reset */}
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            {processed.length} plan{processed.length === 1 ? "" : "s"}
            {filtersActive ? (
              <span className="font-normal text-[var(--color-text-tertiary)]"> of {plans.length}</span>
            ) : null}
          </h3>
          {filtersActive ? (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setEffortSel(new Set());
                setConfSel(new Set());
              }}
              className="text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline"
            >
              Clear filters
            </button>
          ) : null}
        </div>

        {processed.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-6 py-12 text-center text-sm text-[var(--color-text-tertiary)]">
            No plans match these filters.
          </div>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {processed.slice(0, visibleCount).map((plan) => (
                <RefactoringPlanCard
                  key={plan.id}
                  plan={plan}
                  onOpen={openPlan}
                  onAiPrompt={onAiPrompt}
                  highlighted={highlighted === plan.id}
                />
              ))}
            </div>
            {processed.length > visibleCount ? (
              <div className="flex justify-center pt-1">
                <button
                  type="button"
                  onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-sm font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-primary)]"
                >
                  Show {Math.min(PAGE_SIZE, processed.length - visibleCount)} more
                  <span className="text-[var(--color-text-tertiary)]">
                    · {processed.length - visibleCount} left
                  </span>
                </button>
              </div>
            ) : null}
          </>
        )}
      </section>

      <RefactoringModal
        plan={selected}
        open={modalOpen}
        onOpenChange={setModalOpen}
        onAiPrompt={onAiPrompt}
        onGenerateCode={onGenerateCode}
        settingsHref={settingsHref}
        fileHref={fileHref}
      />
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors ${
        active
          ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-primary)]"
      }`}
    >
      {label}
    </button>
  );
}

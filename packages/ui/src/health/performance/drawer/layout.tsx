"use client";

import type { PerformanceModelState, PerformanceOpportunityDetail } from "@repowise-dev/types/health";

import { InfoTip } from "../../../shared/info-tip";

/** The drawer's building blocks: a labelled field, a section, and the notices
 *  that say whether the id still resolves. */

export function Field({
  label,
  value,
  detail,
  hint,
}: {
  label: string;
  value: string;
  detail?: string | undefined;
  /** What the facet means, from the glossary, on hover or tap. */
  hint?: string | undefined;
}) {
  return (
    <div className="min-w-0">
      <dt className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
        {hint ? <InfoTip content={hint} label={`What ${label.toLowerCase()} means`} /> : null}
      </dt>
      <dd className="mt-0.5 text-sm font-medium text-[var(--color-text-primary)]">{value}</dd>
      {detail ? (
        <dd className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">{detail}</dd>
      ) : null}
    </div>
  );
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {title}
      </h4>
      {children}
    </section>
  );
}

/** A resolved id that came from another model is not the same as a wrong id. */
function ModelStateNotice({ state }: { state: PerformanceModelState }) {
  if (state.state === "current") return null;
  const message =
    state.state === "stale_model"
      ? `This id was minted by performance model version ${state.requested_model_version ?? "an earlier release"}; the index now serves version ${state.performance_model_version}.`
      : "This index does not recognize that opportunity id.";
  return (
    <p
      role="status"
      className="border-l-2 border-[var(--color-warning)] py-1.5 pl-3 text-sm text-[var(--color-text-secondary)]"
    >
      {message}
      {state.refresh_required ? " Update the index to get the current grouping." : ""}
    </p>
  );
}

/** Whether the id resolved, and whether anything still supports it. */
export function StatusNotices({ detail }: { detail: PerformanceOpportunityDetail | undefined }) {
  if (!detail) return null;
  if (!detail.found) {
    return (
      <>
        <ModelStateNotice state={detail.model_state} />
        <p className="text-sm text-[var(--color-text-tertiary)]">{detail.detail}</p>
      </>
    );
  }
  return (
    <>
      <ModelStateNotice state={detail.model_state} />
      {detail.lifecycle_status === "resolved" ? (
        <p
          role="status"
          className="border-l-2 border-[var(--color-warning)] py-1.5 pl-3 text-sm text-[var(--color-text-secondary)]"
        >
          No observation supports this cause in the current index. It is kept so a saved
          link still resolves.
        </p>
      ) : null}
    </>
  );
}

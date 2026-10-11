import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

export function PriorityExplanation({ plan }: { plan: RefactoringPlan }) {
  const components = [
    {
      label: "Benefit",
      value: plan.benefit,
      detail: "Health recovered or detector-native gain.",
    },
    {
      label: "Leverage",
      value: plan.leverage,
      detail: "Deficit and dependency or execution centrality.",
    },
    {
      label: "Cost",
      value: plan.cost,
      detail: "Estimated effort and change surface.",
    },
    {
      label: "Risk",
      value: plan.risk,
      detail: "Blast radius, evidence strength, and validation gaps.",
    },
  ];
  const detailed = components.every((item) => typeof item.value === "number");
  return (
    <div>
      <p className="text-sm text-[var(--color-text-secondary)]">
        <span className="font-medium text-[var(--color-text-primary)]">Priority score </span>
        <span className="font-mono tabular-nums">{plan.rank_score.toFixed(4)}</span>. Higher benefit
        and leverage raise priority; cost and risk reduce it.
      </p>
      {detailed ? (
        <dl className="mt-3 grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-4">
          {components.map((item) => (
            <div
              key={item.label}
              className="border-t border-[var(--color-border-default)] py-3 pr-4 first:border-t-0 sm:border-t-0"
            >
              <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                {item.label}
              </dt>
              <dd className="mt-1 font-mono text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
                {item.value!.toFixed(2)}
              </dd>
              <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-tertiary)]">
                {item.detail}
              </p>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
          This server predates the detailed priority components; the compatibility priority is still
          shown.
        </p>
      )}
    </div>
  );
}

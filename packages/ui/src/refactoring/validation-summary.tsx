import type { RecommendationValidation, ValidationBasis } from "@repowise-dev/types/refactoring";

const BASIS_LABEL: Record<ValidationBasis, string> = {
  measured: "Measured coverage",
  inferred: "Inferred reachability",
  mixed: "Mixed evidence",
  unknown: "Validation gap",
};

export interface ValidationSummaryProps {
  validation?: RecommendationValidation | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}

export function ValidationSummary({ validation, fileHref }: ValidationSummaryProps) {
  if (!validation) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">
        Validation detail is unavailable from this older server.
      </p>
    );
  }
  const basis = BASIS_LABEL[validation.basis] ?? validation.basis;
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-[var(--color-text-secondary)]">
          <span className="font-medium text-[var(--color-text-primary)]">{basis}</span>
          {validation.via ? ` via ${validation.via.replace("-", " ")}` : ""}.{" "}
          {validation.total.toLocaleString()} guarding test
          {validation.total === 1 ? "" : "s"}
          {validation.truncated ? `; ${validation.tests.length.toLocaleString()} shown.` : "."}
        </p>
        {validation.basis === "unknown" ? (
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            No measured or inferred guarding test was found. Treat this as explicit validation work.
          </p>
        ) : null}
      </div>

      {validation.tests.length > 0 ? (
        <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
          {validation.tests.map((test) => (
            <li
              key={test}
              className="break-all py-2 font-mono text-xs text-[var(--color-text-secondary)]"
            >
              {test}
            </li>
          ))}
        </ul>
      ) : null}

      {validation.affected_files.length > 0 ? (
        <div>
          <h5 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Affected paths
          </h5>
          <ul className="mt-1.5 space-y-1">
            {validation.affected_files.map((path) => {
              const href = fileHref?.(path, null);
              return (
                <li
                  key={path}
                  className="break-all font-mono text-xs text-[var(--color-text-secondary)]"
                >
                  {href ? (
                    <a
                      href={href}
                      className="underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
                    >
                      {path}
                    </a>
                  ) : (
                    path
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {validation.commands.length > 0 ? (
        <div>
          <h5 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Suggested commands
          </h5>
          <div className="mt-1.5 space-y-1.5">
            {validation.commands.map((command) => (
              <code
                key={command}
                className="block overflow-x-auto whitespace-nowrap bg-[var(--color-bg-inset)] px-3 py-2 font-mono text-xs text-[var(--color-text-secondary)]"
              >
                {command}
              </code>
            ))}
          </div>
          {validation.truncated ? (
            <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
              The command is widened so capped display rows do not become a narrower validation run.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

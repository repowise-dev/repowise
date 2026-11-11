/**
 * How a finished job turned out, beyond its status. A job can complete while
 * its provider rejected every page (each falls back to a placeholder), or
 * while docs were skipped because no provider could be loaded. The server
 * records that as `config.provider_issue`; these helpers read it.
 */

export interface ProviderIssue {
  /** "auth" | "rate_limit" | "unreachable" | "model_not_found" | "not_found" |
   *  "docs_skipped"; a client words known kinds itself and shows `message`
   *  for any other. */
  kind: string;
  /** Plain English hint saying what went wrong and what to do. */
  message: string;
  /** Values the message names, for a client that words it itself. */
  provider?: string | undefined;
  model?: string | undefined;
  target?: string | undefined;
}

interface OutcomeJob {
  status: string;
  failed_pages?: number;
  config?: Record<string, unknown> | null;
}

export type JobOutcome = "completed" | "warning" | "failed" | "cancelled";

export function providerIssue(job: OutcomeJob | null | undefined): ProviderIssue | null {
  const raw = job?.config?.provider_issue;
  if (!raw || typeof raw !== "object") return null;
  const { kind, message, provider, model, target } = raw as Record<string, unknown>;
  if (typeof message !== "string" || !message) return null;
  const text = (v: unknown) => (typeof v === "string" && v ? v : undefined);
  return {
    kind: text(kind) ?? "",
    message,
    provider: text(provider),
    model: text(model),
    target: text(target),
  };
}

/** The terminal outcome, or `null` while the job is still pending or running. */
export function jobOutcome(job: OutcomeJob | null | undefined): JobOutcome | null {
  switch (job?.status) {
    case "completed":
      return (job.failed_pages ?? 0) > 0 || providerIssue(job) ? "warning" : "completed";
    case "failed":
    case "cancelled":
      return job.status;
    default:
      return null;
  }
}

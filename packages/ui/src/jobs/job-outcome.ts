/**
 * How a finished job turned out, beyond its status. A job can complete while
 * its provider rejected every page (each falls back to a placeholder), or
 * while docs were skipped because no provider could be loaded. The server
 * records that as `config.provider_issue`; these helpers read it.
 */

export interface ProviderIssue {
  /** "auth" | "rate_limit" | "unreachable" | "model_not_found" | "docs_skipped" */
  kind: string;
  /** Plain hint saying what went wrong and what to do. */
  message: string;
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
  const { kind, message } = raw as Record<string, unknown>;
  return typeof message === "string" && message
    ? { kind: typeof kind === "string" ? kind : "", message }
    : null;
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

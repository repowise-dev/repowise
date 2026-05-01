/**
 * Indexing / generation job artifact shapes.
 *
 * Mirrors the engine's `JobResponse` Pydantic schema. Used by the dashboard
 * `ActiveJobBanner` and `QuickActions` shells, plus the OSS web `JobLog`
 * family. Hosted-side hooks resolve and forward into these contracts.
 */

export type JobStatus = "pending" | "running" | "completed" | "failed" | "paused";

export interface Job {
  id: string;
  repository_id: string;
  status: JobStatus;
  provider_name: string;
  model_name: string;
  total_pages: number;
  completed_pages: number;
  failed_pages: number;
  current_level: number;
  error_message: string | null;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

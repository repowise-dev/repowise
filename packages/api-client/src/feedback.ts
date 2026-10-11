/**
 * In-app feedback. The web app POSTs to the local server's `/api/feedback`,
 * which forwards the message to the hosted Repowise backend (tagged as OSS).
 */

import { apiPost } from "./client";

/** Feedback categories. Mirrors the server-side `_CATEGORIES` set. */
export type FeedbackCategory = "ui_ux" | "bug" | "feature_request" | "other";

export interface FeedbackInput {
  category: FeedbackCategory;
  message: string;
  /** Optional reply-to address, so the maintainers can follow up. */
  email?: string;
  /** The dashboard page the feedback was sent from. */
  pageUrl?: string;
}

export async function submitFeedback(input: FeedbackInput): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>("/api/feedback", {
    category: input.category,
    message: input.message,
    ...(input.email && { email: input.email }),
    ...(input.pageUrl && { page_url: input.pageUrl }),
  });
}

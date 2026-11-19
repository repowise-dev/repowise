import "./client";
import { apiPost } from "@repowise-dev/api-client";

/**
 * Tell the local server a view was opened. The server checks the id against a
 * fixed list and honours the telemetry opt-out; a failure is never surfaced.
 */
export function recordViewOpened(view: string): void {
  apiPost<void>("/api/telemetry/view", { view }).catch(() => {});
}

import type { DecisionRecord } from "@repowise-dev/types/decisions";

/** What a bulk review run did, record by record. */
export interface BulkReviewResult {
  done: DecisionRecord[];
  failed: { decision: DecisionRecord; message: string }[];
}

/**
 * Run one review write per record, a few at a time, and report each outcome.
 *
 * A loop over the single-record write the host already has, so any backend
 * that can dismiss one record can dismiss a selection. A failure is recorded
 * and the run goes on: one refusal must not leave the rest of the selection
 * unreviewed with nothing said about which went through.
 *
 * Capped concurrency, because a page is at most a few dozen rows and the
 * server serialises writes to one store anyway.
 */
export async function reviewEach(
  decisions: DecisionRecord[],
  write: (decision: DecisionRecord) => Promise<unknown>,
  onProgress?: (settled: number) => void,
  concurrency = 4,
): Promise<BulkReviewResult> {
  const result: BulkReviewResult = { done: [], failed: [] };
  let next = 0;
  let settled = 0;
  const worker = async () => {
    while (next < decisions.length) {
      const decision = decisions[next++] as DecisionRecord;
      try {
        await write(decision);
        result.done.push(decision);
      } catch (err) {
        result.failed.push({
          decision,
          message: err instanceof Error ? err.message : String(err),
        });
      }
      settled += 1;
      onProgress?.(settled);
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, decisions.length) }, worker));
  return result;
}

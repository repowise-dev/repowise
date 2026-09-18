import type {
  DocDriftReferencesResponse,
  DocDriftResponse,
} from "@repowise-dev/types/doc-drift";

/**
 * App-injected data + navigation for the shared {@link DocDriftView}.
 *
 * The view owns the composition — the lede, the confidence split, the filter,
 * the findings table, and every refusal and empty state. The host supplies
 * *how* to fetch and *where* document links go, so web and hosted render the
 * same view from one source.
 *
 * Fetch methods are keyed off `cacheKey` for SWR, so a page-level fetch that
 * shares a key dedupes onto the same request.
 *
 * Only the two required members are ones every host can honestly implement.
 * Dead code made `analyze()` required and hosted had to implement it as a
 * deliberate throw; what a host cannot do is optional here, and the view
 * drops the affordance rather than offering a control that explains its own
 * impossibility after it is pressed.
 */
export interface DocDriftAdapter {
  /** Seeds the view's SWR cache keys — keep it stable per repo/snapshot. */
  cacheKey: string;

  /**
   * Findings, with the summary the server computed over the same query.
   *
   * Never recompute the summary from `findings`: that list is capped by
   * `limit` while `summary.findings_total` counts the whole query, and a
   * client that recomputes reports the page it holds as the repository.
   */
  listFindings(opts?: {
    min_confidence?: number;
    kind?: string;
    limit?: number;
  }): Promise<DocDriftResponse>;

  /** Build an href to a document, at a line when the host supports it. */
  documentHref(path: string, line?: number): string;

  /**
   * Which documents name one file. Optional: a host serving drift from a
   * pre-computed artifact may carry only the findings half, and the view then
   * omits the affordances that would need it.
   */
  listReferences?(target: string): Promise<DocDriftReferencesResponse>;

  /** Navigate to an href (host wires this to its router). Optional: a host
   *  whose document links are plain anchors does not need it. */
  navigate?(href: string): void;
}

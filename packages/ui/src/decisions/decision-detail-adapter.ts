import type { ElementType, ReactNode } from "react";
import type {
  DecisionEvidence,
  DecisionLineageEntry,
  DecisionStatusUpdate,
} from "@repowise-dev/types/decisions";

/** Link element the host injects (e.g. `next/link`); defaults to `<a>`. */
export type DecisionLinkComponent = ElementType<{
  href: string;
  className?: string;
  "aria-label"?: string;
  children: ReactNode;
}>;

/**
 * App-injected data + navigation + slots for the shared {@link DecisionDetail}.
 *
 * The view owns the composition — the markdown body, evolution lineage, the
 * writable module-link editor, the evidence drawer, the AI verification prompt,
 * and the confirm/undo status actions. The host supplies *how* to fetch/mutate,
 * *where* links go, and an optional linked-issues panel (e.g. Jira), so web and
 * hosted render the same view from one source.
 */
export interface DecisionDetailAdapter {
  /** Seeds the view's SWR cache keys — keep it stable per repo + decision. */
  cacheKey: string;
  repoId: string;
  /** Prefix for the evolution lineage links; defaults to `/repos/{repoId}`. */
  linkPrefix?: string;

  /** Supersession/evolution chain, ordered root -> current. */
  getLineage(): Promise<DecisionLineageEntry[]>;
  /** Evidence rows backing the decision — fetched lazily when the drawer opens. */
  getEvidence(): Promise<DecisionEvidence[]>;
  /** Sibling ids in list order, for prev/next navigation. */
  listSiblingIds(): Promise<string[]>;
  /** Module-path suggestions for the linkage editor autocomplete. */
  listModuleSuggestions(): Promise<string[]>;
  /** Persist a status and/or linkage change. */
  patchDecision(patch: DecisionStatusUpdate): Promise<void>;

  /** href to the decisions list. */
  decisionsHref(): string;
  /** href to a sibling decision. */
  decisionHref(decisionId: string): string;
  /** href to the commits view, optionally filtered to a commit or sort. */
  commitsHref(opts?: { commit?: string; sort?: string }): string;
  /** href to the hotspots/churn view for the affected areas. */
  hotspotsHref(): string;

  /** Link element for internal navigation. Defaults to `<a>`. */
  LinkComponent?: DecisionLinkComponent;
  /** Optional linked-issues panel (e.g. Jira). Host supplies or omits. */
  renderLinkedIssues?(): ReactNode;
}

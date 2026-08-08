/**
 * Canonical episode types — the dated things that happened to a repository.
 *
 * Canonical source: engine `EpisodeSummary` / `EpisodeDetail` /
 * `EpisodeListResponse` / `EpisodeCountsResponse` in
 * `packages/server/src/repowise/server/schemas/episodes.py`. Hand-written and
 * hand-kept in sync; nothing generates these.
 *
 * Two shapes because they cost different amounts. A summary is one indexed
 * SQLite read and carries no body; a detail spends one `git rev-list` to
 * answer whether the claim still holds. A list of summaries must never be
 * assembled by fetching details.
 */

/**
 * Only the tiers that describe the repository itself. The engine has a third,
 * `transcript`, which is per-machine and never crosses HTTP — two people
 * opening one dashboard would otherwise see different pages.
 */
export type EpisodeTier = "structural" | "git";

export interface EpisodeSummary {
  id: string;
  tier: EpisodeTier;
  /** Discriminates episodes within a tier, e.g. `code_fix`, `nested_repos`. */
  kind: string;
  subject: string;
  evidence: string;
  /** The bound scope, trimmed. `node_count` is the untrimmed total. */
  nodes: string[];
  /** Files the episode is bound to, before `nodes` was trimmed for display. */
  node_count: number;
  birth_commit: string | null;
  /** ISO-8601 UTC, or null when the store holds no date. */
  birth_at: string | null;
  last_seen_at: string | null;
  /**
   * The currency signal that costs no git call, or `null` on tiers that
   * accumulate members, where the same stamp proves nothing.
   *
   * `null` means *unchecked*, never *stale* — checking happens on the detail
   * route. Required-but-nullable rather than optional, because the engine
   * field carries a default and pydantic serializes defaults: the key is
   * always on the wire. Discriminate on the value, never on key presence.
   */
  still_true: string | null;
}

export interface EpisodeDetail {
  id: string;
  tier: EpisodeTier;
  kind: string;
  subject: string;
  body: string;
  evidence: string;
  nodes: string[];
  node_count: number;
  birth_commit: string | null;
  birth_at: string | null;
  last_seen_at: string | null;
  /** The sentence half of the verdict. Always present on a detail. */
  still_true: string;
  /**
   * The gate half. A reader asking *what happened here* shows the sentence
   * either way; anything putting a claim beside a statement about the present
   * must respect this.
   */
  current: boolean;
}

/**
 * `available: false` means this repository has never derived episodes — a
 * real cold start, not an error and not an empty store. Render it as
 * "nothing here yet", distinct from a store whose rows were all pruned.
 */
export interface EpisodeListResponse {
  available: boolean;
  /** Measured total behind the page, not the length of `episodes`. */
  total: number;
  episodes: EpisodeSummary[];
}

export interface EpisodeCountsResponse {
  available: boolean;
  total: number;
  /** Keys are a subset of the served tiers, so a consumer can switch on them. */
  by_tier: Partial<Record<EpisodeTier, number>>;
  by_kind: Record<string, number>;
}

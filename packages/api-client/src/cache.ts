/**
 * Tiny in-memory cache. Entries are keyed by (repoId, key, tag) where the tag
 * is a freshness stamp such as a head commit or server version. A key holds
 * one tag at a time: storing a new stamp replaces the old entry, so a session
 * that sees many index updates keeps one copy of each payload rather than one
 * per update. No persistence: the cache is rebuilt each session, so it holds
 * nothing that must survive a reload.
 */
export interface RepowiseCache {
  get<T>(repoId: string, key: string, tag: string): T | undefined;
  set<T>(repoId: string, key: string, tag: string, value: T): void;
  /** Drop everything. Called when the index or server identity changes. */
  invalidateAll(): void;
}

function composite(repoId: string, key: string): string {
  return `${repoId}\0${key}`;
}

export function createCache(): RepowiseCache {
  const store = new Map<string, { tag: string; value: unknown }>();
  return {
    get<T>(repoId: string, key: string, tag: string): T | undefined {
      const entry = store.get(composite(repoId, key));
      return entry && entry.tag === tag ? (entry.value as T) : undefined;
    },
    set<T>(repoId: string, key: string, tag: string, value: T): void {
      store.set(composite(repoId, key), { tag, value });
    },
    invalidateAll(): void {
      store.clear();
    },
  };
}

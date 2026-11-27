/**
 * Tiny in-memory cache. Entries are keyed by (repoId, key, tag) where the tag
 * is a freshness stamp such as a head commit or server version. A key holds
 * the two most recently stored tags and drops older ones, so a session that
 * sees many index updates keeps a bounded copy of each payload rather than
 * one per update. Two, not one: a request started before an index update can
 * finish after the fresh one, and its late write must not evict the fresh
 * entry. No persistence: the cache is rebuilt each session, so it holds
 * nothing that must survive a reload.
 */
export interface RepowiseCache {
  get<T>(repoId: string, key: string, tag: string): T | undefined;
  set<T>(repoId: string, key: string, tag: string, value: T): void;
  /** Drop everything. Called when the index or server identity changes. */
  invalidateAll(): void;
}

const TAGS_PER_KEY = 2;

interface Entry {
  tag: string;
  value: unknown;
}

function composite(repoId: string, key: string): string {
  return `${repoId}\0${key}`;
}

export function createCache(): RepowiseCache {
  const store = new Map<string, Entry[]>();
  return {
    get<T>(repoId: string, key: string, tag: string): T | undefined {
      const entry = store.get(composite(repoId, key))?.find((e) => e.tag === tag);
      return entry?.value as T | undefined;
    },
    set<T>(repoId: string, key: string, tag: string, value: T): void {
      const id = composite(repoId, key);
      const entries = (store.get(id) ?? []).filter((e) => e.tag !== tag);
      entries.push({ tag, value });
      store.set(id, entries.slice(-TAGS_PER_KEY));
    },
    invalidateAll(): void {
      store.clear();
    },
  };
}

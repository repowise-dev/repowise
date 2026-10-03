/**
 * Tiny in-memory cache. Entries are keyed by (repoId, key, tag) where the tag
 * is a freshness stamp such as a head commit or server version; when the stamp
 * changes the old entry is simply never read again. No persistence: the cache
 * is rebuilt each session, so it holds nothing that must survive a reload.
 */
export interface RepowiseCache {
  get<T>(repoId: string, key: string, tag: string): T | undefined;
  set<T>(repoId: string, key: string, tag: string, value: T): void;
  /** Drop everything. Called when the index or server identity changes. */
  invalidateAll(): void;
}

function composite(repoId: string, key: string, tag: string): string {
  return `${repoId}\0${key}\0${tag}`;
}

export function createCache(): RepowiseCache {
  const store = new Map<string, unknown>();
  return {
    get<T>(repoId: string, key: string, tag: string): T | undefined {
      return store.get(composite(repoId, key, tag)) as T | undefined;
    },
    set<T>(repoId: string, key: string, tag: string, value: T): void {
      store.set(composite(repoId, key, tag), value);
    },
    invalidateAll(): void {
      store.clear();
    },
  };
}

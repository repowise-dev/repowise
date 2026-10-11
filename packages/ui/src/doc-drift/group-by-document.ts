export interface DocumentGroup<T> {
  document: string;
  items: T[];
}

/** One group per document in first-seen order, lines ascending within. */
export function groupByDocument<T>(
  items: readonly T[],
  documentOf: (item: T) => string,
  lineOf: (item: T) => number,
): DocumentGroup<T>[] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = documentOf(item);
    const list = groups.get(key);
    if (list) list.push(item);
    else groups.set(key, [item]);
  }
  return [...groups].map(([document, list]) => ({
    document,
    items: list.sort((a, b) => lineOf(a) - lineOf(b)),
  }));
}

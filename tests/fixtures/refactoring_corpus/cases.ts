// Extract Method soundness archetypes (TypeScript): the same gates on a
// second grammar. `// unsound:` marks a region the slicer must never offer,
// `// sound:` one it must keep offering.

function record(value: number): number {
  return value;
}

export function cleanExtraction(rows: number[], threshold: number): number {
  let total = 0;
  let count = 0;
  for (const row of rows) {
    total += row;
    count += 1;
  }
  // sound: unconditional OUT, no enclosing loop
  let average = 0;
  if (count) {
    average = total / count;
  } else {
    average = 0;
  }
  record(average);
  record(total);
  return average;
}

export function ifWithoutElse(values: number[], limit: number): string {
  let label = "unset";
  for (const value of values) {
    record(value);
    // unsound: no else, so one path leaves label untouched
    if (value > limit) {
      record(value);
      record(limit);
      label = "high";
    }
    record(limit);
    record(value);
  }
  return label;
}

export function elseAssignedTwin(values: number[], limit: number): string {
  let label = "unset";
  for (const value of values) {
    record(value);
    // sound: every path writes label
    if (value > limit) {
      record(value);
      label = "high";
    } else {
      record(limit);
      label = "low";
    }
    record(limit);
    record(value);
  }
  return label;
}

export function loopMutatesIterated(queue: number[], limit: number): number {
  let seen = 0;
  for (const item of queue) {
    record(item);
    // unsound: the span mutates the collection the loop iterates
    if (item > limit) {
      queue.pop();
      seen = seen + 1;
    } else {
      queue.push(item);
      seen = seen + 2;
    }
    record(limit);
    record(item);
  }
  return seen;
}

export function sameLineSibling(values: number[], limit: number): string {
  let label = "unset";
  for (const value of values) {
    record(value);
    // unsound: label is written on one branch only, and the sibling call on
    // that same line must not stand in as proof that it was
    if (value > limit) { label = "high"; } record(limit);
    record(
      value
    );
    record(
      limit
    );
  }
  return label;
}

export function elseIfChain(values: number[], limit: number): string {
  let label = "unset";
  for (const value of values) {
    record(value);
    // sound: exhaustive if / else if / else
    if (value > limit) {
      record(value);
      label = "high";
    } else if (value === limit) {
      record(limit);
      label = "equal";
    } else {
      record(0);
      label = "low";
    }
    record(limit);
    record(value);
  }
  return label;
}

// A member named like the iterated collection is not that collection.
export function memberNameCollision(pages: number[], limit: number): number {
  const bucket = { pages: [] as number[], total: 0 };
  for (const page of pages) {
    record(page);
    // sound: the receiver is bucket, not the iterated pages
    if (page > limit) {
      bucket.pages.push(page);
      bucket.total = bucket.total + 1;
    } else {
      bucket.pages.push(limit);
      bucket.total = bucket.total + 2;
    }
    record(limit);
    record(page);
  }
  return bucket.total;
}

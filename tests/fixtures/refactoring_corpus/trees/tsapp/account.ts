// Supporting module for the barrel archetype, and a second archetype of its
// own: a long branch-heavy function that the Extract Method gate can reason
// about, so the tree exercises composition on TypeScript rather than only on
// Python.

export interface Account {
  id: string;
  displayName: string;
  email: string;
  status: string;
  balanceCents: number;
  tags: string[];
}

export function parseAccount(raw: Record<string, unknown>): Account {
  const id = typeof raw.id === "string" ? raw.id.trim() : "";
  if (!id) {
    throw new Error("account requires an id");
  }
  let displayName = "";
  if (typeof raw.displayName === "string") {
    displayName = raw.displayName.trim();
  } else if (typeof raw.name === "string") {
    displayName = raw.name.trim();
  } else {
    displayName = id;
  }
  let email = "";
  if (typeof raw.email === "string" && raw.email.includes("@")) {
    email = raw.email.trim().toLowerCase();
  } else {
    email = "";
  }
  let status = "open";
  if (typeof raw.status === "string" && raw.status.length > 0) {
    status = raw.status.trim().toLowerCase();
  }
  let balanceCents = 0;
  if (typeof raw.balanceCents === "number" && Number.isFinite(raw.balanceCents)) {
    balanceCents = Math.round(raw.balanceCents);
  } else if (typeof raw.balance === "number" && Number.isFinite(raw.balance)) {
    balanceCents = Math.round(raw.balance * 100);
  } else {
    balanceCents = 0;
  }
  const tags: string[] = [];
  if (Array.isArray(raw.tags)) {
    for (const tag of raw.tags) {
      if (typeof tag === "string" && tag.trim().length > 0) {
        tags.push(tag.trim().toLowerCase());
      }
    }
  }
  tags.sort();
  return { id, displayName, email, status, balanceCents, tags };
}

export function formatAccount(account: Account): string {
  const parts: string[] = [];
  parts.push(`id=${account.id}`);
  if (account.displayName) {
    parts.push(`name=${account.displayName}`);
  }
  if (account.email) {
    parts.push(`email=${account.email}`);
  }
  parts.push(`status=${account.status}`);
  parts.push(`balance=${(account.balanceCents / 100).toFixed(2)}`);
  if (account.tags.length > 0) {
    parts.push(`tags=${account.tags.join("|")}`);
  }
  return parts.join(" ");
}

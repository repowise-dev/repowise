// Supporting module for the barrel archetype, and deliberately a near-duplicate
// of account.ts: a real cross-file clone with no co-change history behind it,
// which is the shape the composer demotes to evidence rather than instructing a
// shared helper over.

export interface Workspace {
  id: string;
  displayName: string;
  email: string;
  status: string;
  seatCount: number;
  tags: string[];
}

export function parseWorkspace(raw: Record<string, unknown>): Workspace {
  const id = typeof raw.id === "string" ? raw.id.trim() : "";
  if (!id) {
    throw new Error("workspace requires an id");
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
  let seatCount = 0;
  if (typeof raw.seatCount === "number" && Number.isFinite(raw.seatCount)) {
    seatCount = Math.round(raw.seatCount);
  } else if (typeof raw.seats === "number" && Number.isFinite(raw.seats)) {
    seatCount = Math.round(raw.seats * 100);
  } else {
    seatCount = 0;
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
  return { id, displayName, email, status, seatCount, tags };
}

export function formatWorkspace(workspace: Workspace): string {
  const parts: string[] = [];
  parts.push(`id=${workspace.id}`);
  if (workspace.displayName) {
    parts.push(`name=${workspace.displayName}`);
  }
  if (workspace.email) {
    parts.push(`email=${workspace.email}`);
  }
  parts.push(`status=${workspace.status}`);
  parts.push(`seats=${(workspace.seatCount / 100).toFixed(2)}`);
  if (workspace.tags.length > 0) {
    parts.push(`tags=${workspace.tags.join("|")}`);
  }
  return parts.join(" ");
}

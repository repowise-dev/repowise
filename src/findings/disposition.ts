export type FindingDisposition = "open" | "accepted" | "declined";

export interface Finding {
  id: string;
  fingerprint: string;
  disposition: FindingDisposition;
  [key: string]: unknown;
}

const terminalDispositions = new Set<FindingDisposition>(["accepted", "declined"]);

/** Preserve a reviewer's decision when an analysis re-discovers a finding. */
export function reconcileFinding(existing: Finding | undefined, discovered: Finding): Finding {
  if (!existing) return discovered;

  if (existing.disposition === "declined" && discovered.disposition === "open") {
    return { ...existing, disposition: existing.disposition };
  }

  return {
    ...discovered,
    id: existing.id,
    disposition: terminalDispositions.has(existing.disposition)
      ? existing.disposition
      : discovered.disposition,
  };
}

export function setDisposition(
  finding: Finding,
  disposition: FindingDisposition,
): Finding {
  return { ...finding, disposition };
}

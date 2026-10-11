import type { ChatArtifact } from "@repowise-dev/types/chat";

export interface ArtifactSourceTarget {
  pageId?: string;
  path?: string;
}

/** Resolve the canonical source reference shapes emitted by MCP tools. */
export function getArtifactSourceTarget(artifact: ChatArtifact): ArtifactSourceTarget | null {
  const data = artifact.data as unknown as Record<string, unknown>;
  const pageId = typeof data.page_id === "string" ? data.page_id : undefined;
  const targets = data.targets && typeof data.targets === "object" && !Array.isArray(data.targets)
    ? Object.keys(data.targets as Record<string, unknown>)
    : [];
  const candidates = Array.isArray(data.candidates) ? data.candidates : [];
  const candidatePath = candidates.find((candidate) => candidate && typeof candidate === "object" && typeof (candidate as Record<string, unknown>).file === "string") as Record<string, unknown> | undefined;
  const path = [data.file, data.file_path, data.path, data.target, targets[0], candidatePath?.file]
    .find((value): value is string => typeof value === "string" && value.length > 0);
  return pageId || path ? { ...(pageId ? { pageId } : {}), ...(path ? { path } : {}) } : null;
}

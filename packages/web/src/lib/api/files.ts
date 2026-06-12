import type { FileDetailResponse } from "@repowise-dev/types/files";
import { apiGet } from "./client";

/** Canonical file-detail aggregate for the file entity page. */
export async function getFileDetail(
  repoId: string,
  filePath: string,
): Promise<FileDetailResponse> {
  // Encode each segment but keep the slashes — the server route uses a
  // catch-all path converter.
  const encoded = filePath.split("/").map(encodeURIComponent).join("/");
  return apiGet<FileDetailResponse>(`/api/repos/${repoId}/files/${encoded}`);
}

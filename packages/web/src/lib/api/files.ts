import type { FileDetailResponse } from "@repowise-dev/types/files";
import { apiGet, BASE_URL, buildHeaders } from "./client";

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

/** Raw file content from the repo checkout (plain text, not JSON). */
export async function getFileContent(repoId: string, filePath: string): Promise<string> {
  const url = new URL(
    `${BASE_URL}/api/repos/${repoId}/file-content`,
    typeof window !== "undefined" ? window.location.href : "http://localhost",
  );
  url.searchParams.set("file_path", filePath);
  const res = await fetch(url.toString(), { headers: buildHeaders() });
  if (!res.ok) throw new Error(`Failed to fetch file content (${res.status})`);
  return res.text();
}

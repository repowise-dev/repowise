import type { FileDetailResponse, FilesIndexResponse } from "@repowise-dev/types/files";
import { apiGet, BASE_URL, buildHeaders } from "./client";

/** Slim per-file rows for the browsable Files index + treemap. */
export async function getFilesIndex(repoId: string): Promise<FilesIndexResponse> {
  return apiGet<FilesIndexResponse>(`/api/repos/${repoId}/files`);
}

/** Canonical file-detail aggregate for the file entity page.
 *
 *  `fields: "slim"` drops the four unbounded payloads (wiki body, coverage
 *  line array, per-function blame, per-finding `details`) for a caller that
 *  only renders the summary numbers. */
export async function getFileDetail(
  repoId: string,
  filePath: string,
  opts?: { fields?: "full" | "slim" },
): Promise<FileDetailResponse> {
  // Encode each segment but keep the slashes — the server route uses a
  // catch-all path converter.
  const encoded = filePath.split("/").map(encodeURIComponent).join("/");
  const qs = opts?.fields ? `?fields=${opts.fields}` : "";
  return apiGet<FileDetailResponse>(`/api/repos/${repoId}/files/${encoded}${qs}`);
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

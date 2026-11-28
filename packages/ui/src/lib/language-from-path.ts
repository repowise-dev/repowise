/** Code-block language id for a file path, read off its extension ("text" when unknown). */

const EXTENSION_MAP: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  rs: "rust",
  go: "go",
  rb: "ruby",
  java: "java",
  cs: "csharp",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  c: "c",
  h: "c",
  css: "css",
  html: "html",
  htm: "html",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  md: "markdown",
  sh: "shell",
  bash: "shell",
  sql: "sql",
  xml: "xml",
  toml: "toml",
  swift: "swift",
  kt: "kotlin",
  kts: "kotlin",
  r: "r",
  R: "r",
  php: "php",
};

export function getLanguageFromPath(filePath: string): string {
  const parts = filePath.split(".");
  if (parts.length < 2) return "text";
  const ext = parts[parts.length - 1];
  if (ext === undefined) return "text";
  return EXTENSION_MAP[ext] ?? "text";
}

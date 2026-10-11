/**
 * Fine-grained syntax highlighter for the docs webview.
 *
 * The shared markdown renderer highlights fenced code with `import("shiki")`,
 * whose meta bundle statically re-exports every grammar and theme shiki ships
 * (hundreds of languages, ~10 MB of lazy chunks). Inside the packaged extension
 * that whole registry rides along in the VSIX even though a repo's docs only
 * ever touch a handful of languages and two themes. The webview Vite build
 * aliases the bare `shiki` specifier to this module, so only the curated
 * language set below is bundled; anything outside it renders as plain
 * monospace. The JavaScript regex engine replaces the ~600 KB wasm oniguruma
 * engine (the CSP blocks runtime wasm fetches anyway).
 *
 * The exported `codeToHtml` matches the singleton shiki calls the renderer
 * makes: `codeToHtml(code, { lang, themes: { light, dark }, defaultColor })`.
 */
import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import githubLight from "@shikijs/themes/github-light";
import vesper from "@shikijs/themes/vesper";

/** Languages bundled into the webview. Keep this list to what a code wiki
 *  realistically contains; each entry adds its grammar chunk to the VSIX. */
const LANG_LOADERS: Record<string, () => Promise<{ default: unknown }>> = {
  typescript: () => import("@shikijs/langs/typescript"),
  tsx: () => import("@shikijs/langs/tsx"),
  javascript: () => import("@shikijs/langs/javascript"),
  jsx: () => import("@shikijs/langs/jsx"),
  python: () => import("@shikijs/langs/python"),
  json: () => import("@shikijs/langs/json"),
  yaml: () => import("@shikijs/langs/yaml"),
  bash: () => import("@shikijs/langs/bash"),
  shellscript: () => import("@shikijs/langs/shellscript"),
  markdown: () => import("@shikijs/langs/markdown"),
  html: () => import("@shikijs/langs/html"),
  xml: () => import("@shikijs/langs/xml"),
  css: () => import("@shikijs/langs/css"),
  scss: () => import("@shikijs/langs/scss"),
  sql: () => import("@shikijs/langs/sql"),
  go: () => import("@shikijs/langs/go"),
  rust: () => import("@shikijs/langs/rust"),
  java: () => import("@shikijs/langs/java"),
  c: () => import("@shikijs/langs/c"),
  cpp: () => import("@shikijs/langs/cpp"),
  csharp: () => import("@shikijs/langs/csharp"),
  ruby: () => import("@shikijs/langs/ruby"),
  php: () => import("@shikijs/langs/php"),
  toml: () => import("@shikijs/langs/toml"),
  ini: () => import("@shikijs/langs/ini"),
  dockerfile: () => import("@shikijs/langs/dockerfile"),
  diff: () => import("@shikijs/langs/diff"),
  kotlin: () => import("@shikijs/langs/kotlin"),
  swift: () => import("@shikijs/langs/swift"),
};

/** Common fenced-block aliases mapped onto the canonical grammar name. */
const ALIASES: Record<string, string> = {
  ts: "typescript",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  py3: "python",
  sh: "bash",
  shell: "bash",
  zsh: "bash",
  yml: "yaml",
  rs: "rust",
  golang: "go",
  "c++": "cpp",
  cxx: "cpp",
  "c#": "csharp",
  cs: "csharp",
  md: "markdown",
  markdown: "markdown",
  rb: "ruby",
  htm: "html",
  docker: "dockerfile",
  kt: "kotlin",
};

let highlighterPromise: Promise<HighlighterCore> | null = null;
function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighterCore({
      themes: [githubLight, vesper],
      engine: createJavaScriptRegexEngine(),
    });
  }
  return highlighterPromise;
}

// One in-flight load per language so concurrent code blocks don't double-register.
const langLoads = new Map<string, Promise<void>>();
function ensureLanguage(highlighter: HighlighterCore, lang: string): Promise<void> {
  let load = langLoads.get(lang);
  if (!load) {
    const loader = LANG_LOADERS[lang];
    load = loader
      ? loader().then((mod) => {
          highlighter.loadLanguage(mod.default as never);
        })
      : Promise.resolve();
    langLoads.set(lang, load);
  }
  return load;
}

interface CodeToHtmlOptions {
  lang?: string;
  themes?: { light: string; dark: string };
  defaultColor?: string | false;
}

export async function codeToHtml(code: string, options: CodeToHtmlOptions): Promise<string> {
  const requested = String(options.lang ?? "text").toLowerCase();
  const canonical = ALIASES[requested] ?? requested;
  const highlighter = await getHighlighter();

  let lang = "text";
  if (LANG_LOADERS[canonical]) {
    await ensureLanguage(highlighter, canonical);
    lang = canonical;
  }

  return highlighter.codeToHtml(code, { ...options, lang } as never);
}

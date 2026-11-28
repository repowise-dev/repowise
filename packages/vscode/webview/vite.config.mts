import * as path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

/** One entry per view; the host HTML loads dist/webview/<view>.js. */
const VIEWS = [
  "home",
  "health",
  "architecture",
  "graph",
  "refactoring",
  "decisions",
  "docs",
  "risk",
  "settings",
] as const;

export default defineConfig({
  root: here,
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: [
      // The shared UI package peers on next-themes; inside a webview the
      // editor owns the theme, so the hook is served by a local shim.
      { find: "next-themes", replacement: path.resolve(here, "src/shims/next-themes.ts") },
      // The bare `shiki` meta bundle ships every grammar; the shim narrows it
      // to a curated language set. Anchored regex so `shiki/core` and the
      // fine-grained subpaths the shim itself imports still resolve normally.
      { find: /^shiki$/, replacement: path.resolve(here, "src/shims/shiki.ts") },
      // Geist font files, bundled by theme-bridge.css. CSS url() does not
      // resolve bare package paths and the package exports no file paths, so
      // its font folder is located from an exported entry and given a name.
      // Tailwind's import pass rebases the url to "./geist-fonts/...", hence
      // the optional prefix.
      {
        find: /^(\.\/)?geist-fonts\//,
        replacement: `${path.join(path.dirname(require.resolve("geist/font/sans")), "fonts")}/`,
      },
    ],
  },
  // Asset urls in the stylesheet (the bundled fonts) resolve relative to
  // webview.css: the webview serves dist/webview under its own resource
  // origin, where a root-absolute "/assets/..." points nowhere.
  experimental: {
    renderBuiltUrl: (_filename, { hostType }) => (hostType === "css" ? { relative: true } : undefined),
  },
  build: {
    outDir: path.resolve(here, "../dist/webview"),
    emptyOutDir: true,
    target: "chrome120",
    // One stylesheet for all views: the palette is global anyway, and a
    // fixed name keeps the host HTML builder trivial.
    cssCodeSplit: false,
    sourcemap: false,
    rollupOptions: {
      input: Object.fromEntries(
        VIEWS.map((view) => [view, path.resolve(here, `src/views/${view}/main.tsx`)]),
      ),
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: (info) =>
          info.names?.some((n) => n.endsWith(".css"))
            ? "webview.css"
            : "assets/[name]-[hash][extname]",
      },
      onwarn(warning, warn) {
        // "use client" directives from the shared UI sources are meaningless
        // outside an RSC bundler and safe to drop silently.
        if (warning.code === "MODULE_LEVEL_DIRECTIVE") return;
        warn(warning);
      },
    },
  },
});

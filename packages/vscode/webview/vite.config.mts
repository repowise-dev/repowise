import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const here = path.dirname(fileURLToPath(import.meta.url));

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
] as const;

export default defineConfig({
  root: here,
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: [
      // The shared UI package peers on next-themes; inside a webview the
      // editor owns the theme, so the hook is served by a local shim.
      { find: "next-themes", replacement: path.resolve(here, "src/shims/next-themes.ts") },
      // The Lottie loader fetches WASM + JSON at runtime; both are blocked by
      // the webview CSP, so loading states render a CSS spinner instead.
      {
        find: "@lottiefiles/dotlottie-react",
        replacement: path.resolve(here, "src/shims/dotlottie-react.tsx"),
      },
      // The bare `shiki` meta bundle ships every grammar; the shim narrows it
      // to a curated language set. Anchored regex so `shiki/core` and the
      // fine-grained subpaths the shim itself imports still resolve normally.
      { find: /^shiki$/, replacement: path.resolve(here, "src/shims/shiki.ts") },
    ],
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

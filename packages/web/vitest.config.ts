import { defineConfig } from "vitest/config";
import { createRequire } from "node:module";
import path from "node:path";

// Resolves to the library's real entry file. The alias below maps the bare
// `@testing-library/react` specifier onto the wrapper, so the wrapper cannot
// import it by package name (that would be self-referential) and needs the
// file path instead. Resolved rather than hard-coded: npm may hoist the
// package to the workspace root, so a relative path is not stable.
const require = createRequire(import.meta.url);

export default defineConfig({
  // `jsx: "preserve"` in tsconfig leaves esbuild on the classic transform, so
  // any component that does not import React itself fails to render under
  // test. Next compiles the app with the automatic runtime; match it here.
  esbuild: { jsx: "automatic", jsxImportSource: "react" },
  test: {
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  resolve: {
    alias: [
      // Order matters: the wrapper's private specifier is matched before the
      // generic "@/" entry, which would otherwise swallow it.
      {
        find: "@rtl-real",
        replacement: require.resolve("@testing-library/react/dist/index.js"),
      },
      // Components read their copy through `useTranslations`, which throws
      // outside a provider. Route every test's `render` through a wrapper that
      // mounts the same `<NextIntlClientProvider>` `app/layout.tsx` does.
      {
        find: "@testing-library/react",
        replacement: path.resolve(__dirname, "src/test/testing-library.tsx"),
      },
      { find: /^@\//, replacement: path.resolve(__dirname, "src") + "/" },
    ],
  },
});

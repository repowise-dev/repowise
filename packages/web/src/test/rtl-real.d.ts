/**
 * Types for the `@rtl-real` specifier.
 *
 * `vitest.config.ts` aliases `@testing-library/react` onto
 * `src/test/testing-library.tsx`, so that wrapper cannot import the library by
 * package name (it would resolve to itself). It imports `@rtl-real` instead,
 * which the same config points at the library's real entry file. TypeScript
 * does not read that config, so the specifier is declared here as an alias of
 * the package it stands for.
 */
declare module "@rtl-real" {
  export * from "@testing-library/react";
}

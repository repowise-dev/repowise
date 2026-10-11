import * as React from "react";
// Through a private specifier: `@testing-library/react` is aliased to this
// very file by `vitest.config.ts`, so importing it by package name here would
// be self-referential.
import {
  render as rtlRender,
  type RenderOptions,
  type RenderResult,
} from "@rtl-real";
import { NextIntlClientProvider } from "next-intl";
import messages from "../../messages/en.json";

/**
 * `@testing-library/react` with next-intl wired in.
 *
 * Components read their copy through `useTranslations`, which throws when it
 * is not inside a provider (next-intl "missing-context"). The app mounts
 * `<NextIntlClientProvider>` in `app/layout.tsx`; a unit test that renders a
 * component directly has to do the same, or every such suite fails for a
 * reason that has nothing to do with the code under test.
 *
 * `vitest.config.ts` aliases `@testing-library/react` here, so every test gets
 * the provider without each one repeating the wrapper. An explicit `wrapper`
 * option is still honoured (it is nested inside the provider).
 */
export function render(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, "wrapper"> & { wrapper?: React.ComponentType<{ children: React.ReactNode }> },
): RenderResult {
  const { wrapper: Outer, ...rest } = options ?? {};
  const Providers = ({ children }: { children: React.ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages}>
      {Outer ? <Outer>{children}</Outer> : children}
    </NextIntlClientProvider>
  );
  return rtlRender(ui, { ...rest, wrapper: Providers });
}

export * from "@rtl-real";

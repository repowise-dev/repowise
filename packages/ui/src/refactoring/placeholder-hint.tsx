import { HELPER_NAME_PLACEHOLDER, HELPER_TYPE_PLACEHOLDER } from "./types";

/** Says how to fill the `<name>` / `<type>` placeholders in a helper's
 * header or call, when any of `texts` uses one; nothing otherwise. */
export function PlaceholderHint({ texts }: { texts: string[] }) {
  const used = texts.some(
    (t) => t.includes(HELPER_NAME_PLACEHOLDER) || t.includes(HELPER_TYPE_PLACEHOLDER),
  );
  if (!used) return null;
  return (
    <p className="mt-2 text-2xs text-[var(--color-text-tertiary)]">
      Replace {HELPER_NAME_PLACEHOLDER} with a name for what the lines do, and any{" "}
      {HELPER_TYPE_PLACEHOLDER} with the value's type.
    </p>
  );
}

import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge resolves conflicts by class group, and it only knows the
 * default font-size scale. `text-caption` is ours (globals.css), so unknown,
 * and an arbitrary colour such as `text-[var(--color-text-tertiary)]` later
 * in the list dropped it as a clashing `text-*`. The element then inherited
 * the font size of whatever held it. Naming the size here keeps the two in
 * their own groups.
 */
const twMerge = extendTailwindMerge({
  extend: { theme: { text: ["caption"] } },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

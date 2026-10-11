/**
 * Which command palette owns ⌘K.
 *
 * Two palettes can be on screen at once: the app-wide one, mounted by the root
 * layout on every route, and a scoped one that searches only what the current
 * surface holds. Both used to bind ⌘K on `window`, so on a surface with a
 * scoped palette the shortcut opened both dialogs stacked on top of each other.
 *
 * A scoped palette claims the shortcut while it is mounted; the app-wide one
 * stands down while the claim is held. Ownership follows what is mounted rather
 * than the URL, so a surface that grows or loses its own palette needs no
 * change here.
 *
 * The claim lives on `document.body` rather than in a module variable
 * deliberately. The two palettes are in different packages, and a bundler that
 * gave them separate copies of this module would leave the app-wide palette
 * reading a counter the scoped one never incremented — the shortcut would
 * silently break again, in exactly the way that is hardest to notice. The DOM
 * is one instance no matter how the modules are resolved.
 */

const ATTR = "data-command-palette-scope";
const COUNT_ATTR = "data-command-palette-holders";

function holders(): number {
  const raw = document.body.getAttribute(COUNT_ATTR);
  const n = raw === null ? 0 : Number.parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function setHolders(n: number, scope: string | null): void {
  if (n <= 0) {
    document.body.removeAttribute(COUNT_ATTR);
    document.body.removeAttribute(ATTR);
    return;
  }
  document.body.setAttribute(COUNT_ATTR, String(n));
  if (scope !== null) document.body.setAttribute(ATTR, scope);
}

/**
 * Claim ⌘K for a scoped palette. Returns the release function to call on
 * unmount.
 *
 * Counted rather than boolean because React can mount the next instance before
 * unmounting the previous one, and a boolean would leave the shortcut released
 * while a palette was still on screen.
 */
export function claimCommandPaletteShortcut(scope: string): () => void {
  const held = holders();
  const previous = document.body.getAttribute(ATTR);
  if (held > 0 && previous !== null && previous !== scope) {
    // Two different surfaces both think they own the shortcut. Whichever
    // claimed last wins, and the other one's palette becomes unreachable by
    // keyboard — worth a line in the console rather than a silent surprise.
    console.warn(
      `[command-palette] "${scope}" claimed the shortcut while "${previous}" still holds it`,
    );
  }
  setHolders(held + 1, scope);

  let released = false;
  return () => {
    if (released) {
      console.warn(`[command-palette] "${scope}" released the shortcut twice`);
      return;
    }
    released = true;
    setHolders(holders() - 1, document.body.getAttribute(ATTR));
  };
}

/** Whether a scoped palette currently owns ⌘K. */
export function commandPaletteShortcutIsClaimed(): boolean {
  return holders() > 0;
}

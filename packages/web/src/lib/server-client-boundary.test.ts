import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { changeLabel } from "./changes";

const SRC = path.resolve(__dirname, "..");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

const isClient = (source: string) => /^\s*(\/\/[^\n]*\n|\/\*[\s\S]*?\*\/\s*)*["']use client["']/.test(source);

function resolveAlias(spec: string): string | null {
  const base = path.join(SRC, spec.slice(2));
  for (const candidate of [`${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

/**
 * A server component may render a client component, but calling a plain
 * function exported from a "use client" module throws at request time
 * ("Attempted to call X() from the server"). Every value a server file under
 * `app/` imports from a client module must therefore be a component.
 */
describe("server/client module boundary", () => {
  it("server files under app/ import only components from client modules", () => {
    const offenders: string[] = [];
    for (const file of walk(path.join(SRC, "app"))) {
      const source = readFileSync(file, "utf8");
      if (isClient(source)) continue;
      for (const m of source.matchAll(/import\s+(type\s+)?\{([^}]*)\}\s+from\s+["'](@\/[^"']+)["']/g)) {
        if (m[1]) continue;
        const target = resolveAlias(m[3]!);
        if (!target || !isClient(readFileSync(target, "utf8"))) continue;
        for (const raw of m[2]!.split(",")) {
          const name = raw.trim();
          if (!name || name.startsWith("type ")) continue;
          const local = name.split(/\s+as\s+/)[0]!;
          if (!/^[A-Z]/.test(local)) offenders.push(`${path.relative(SRC, file)}: ${local} from ${m[3]}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("keeps the change helpers in a module a server component can call", () => {
    expect(isClient(readFileSync(path.join(SRC, "lib/changes.ts"), "utf8"))).toBe(false);
    expect(changeLabel({ forge_ref: "#42", id: "pr-42" })).toBe("#42");
    expect(changeLabel({ forge_ref: null, id: "commit-0123456789abcdef0123" })).toBe("commit-0123456789ab");
  });
});

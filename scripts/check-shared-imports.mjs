#!/usr/bin/env node
/**
 * Fails if any file under packages/ui/src or packages/api-client/src imports
 * "next" (or a "next/..." subpath). Those packages are shared with consumers
 * that are not Next.js apps, so they must stay framework-free. "next-themes"
 * is a different package and must not be flagged.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = process.cwd();
const SCAN_DIRS = ["packages/ui/src", "packages/api-client/src"];
const FILE_EXTENSIONS = [".ts", ".tsx"];

// Matches import/export "next", "next/x", require("next"), require("next/x"),
// and dynamic import("next"...). Captures the specifier's leading segment so
// "next-themes" (a distinct package name) never matches.
const NEXT_IMPORT_PATTERN =
  /(?:from\s+|require\(\s*|import\(\s*)["']next(?:\/[^"']*)?["']/g;

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      walk(fullPath, files);
    } else if (FILE_EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      files.push(fullPath);
    }
  }
  return files;
}

const offenses = [];

for (const scanDir of SCAN_DIRS) {
  const absDir = join(ROOT, scanDir);
  let files;
  try {
    files = walk(absDir);
  } catch {
    continue; // directory doesn't exist, nothing to scan
  }

  for (const file of files) {
    const content = readFileSync(file, "utf8");
    const lines = content.split("\n");
    lines.forEach((line, index) => {
      NEXT_IMPORT_PATTERN.lastIndex = 0;
      if (NEXT_IMPORT_PATTERN.test(line)) {
        offenses.push({
          file: relative(ROOT, file),
          line: index + 1,
          text: line.trim(),
        });
      }
    });
  }
}

if (offenses.length > 0) {
  console.error("Found forbidden \"next\" imports in shared packages:\n");
  for (const offense of offenses) {
    console.error(`  ${offense.file}:${offense.line}  ${offense.text}`);
  }
  console.error(
    `\n${offenses.length} offense(s). packages/ui and packages/api-client must stay framework-free; remove the Next.js dependency.`,
  );
  process.exit(1);
}

console.log("No forbidden \"next\" imports found in shared packages.");
process.exit(0);

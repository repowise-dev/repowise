/**
 * Node-id helpers. These mirror the Python module, so the cases that matter
 * are the ones that used to be got wrong by hand: hardcoded prefix lengths,
 * and treating a Windows drive letter as a prefix.
 */
import { describe, it, expect } from "vitest";
import {
  displayLabel,
  filePathOf,
  isExternal,
  nodeKind,
  stripPrefix,
  symbolNameOf,
} from "../src/node-ids";
import fixture from "../../../tests/fixtures/node_ids.json";

describe("nodeKind", () => {
  it("recognises the prefixes the backend emits", () => {
    expect(nodeKind("external:react")).toBe("external");
    expect(nodeKind("framework:typo3-core")).toBe("framework");
    expect(nodeKind("pkg:packages/core")).toBe("pkg");
    expect(nodeKind("cmp:packages/core/ingestion")).toBe("cmp");
    expect(nodeKind("file:src/main.py")).toBe("file");
  });

  it("treats path::Symbol as a symbol", () => {
    expect(nodeKind("src/main.py::main")).toBe("symbol");
  });

  it("treats an unknown prefix as a plain path", () => {
    expect(nodeKind("src/main.py")).toBe("path");
    expect(nodeKind("C:\\src\\main.py")).toBe("path");
    expect(nodeKind("mailto:someone@example.com")).toBe("path");
  });
});

describe("stripPrefix", () => {
  it("does not depend on the prefix's length", () => {
    expect(stripPrefix("external:react")).toBe("react");
    expect(stripPrefix("ext:react")).toBe("react");
    expect(stripPrefix("pkg:packages/core")).toBe("packages/core");
  });

  it("leaves an unprefixed id alone", () => {
    expect(stripPrefix("src/main.py")).toBe("src/main.py");
    expect(stripPrefix("C:\\src\\main.py")).toBe("C:\\src\\main.py");
  });

  it("keeps colons that belong to the value", () => {
    expect(stripPrefix("external:pub:http")).toBe("pub:http");
  });
});

describe("filePathOf", () => {
  it("resolves symbols to their file", () => {
    expect(filePathOf("src/main.py::main")).toBe("src/main.py");
  });

  it("resolves file ids and bare paths to themselves", () => {
    expect(filePathOf("file:src/main.py")).toBe("src/main.py");
    expect(filePathOf("src/main.py")).toBe("src/main.py");
  });

  it("returns null for things that are not files", () => {
    expect(filePathOf("external:react")).toBeNull();
    expect(filePathOf("pkg:packages/core")).toBeNull();
  });
});

describe("symbolNameOf", () => {
  it("returns the name after the separator", () => {
    expect(symbolNameOf("src/main.py::main")).toBe("main");
    expect(symbolNameOf("src/main.py::Klass.method")).toBe("Klass.method");
  });

  it("returns null when there is no symbol", () => {
    expect(symbolNameOf("src/main.py")).toBeNull();
  });
});

describe("displayLabel", () => {
  it("prefers the symbol name, then the basename", () => {
    expect(displayLabel("src/main.py::main")).toBe("main");
    expect(displayLabel("src/deep/main.py")).toBe("main.py");
    expect(displayLabel("external:react")).toBe("react");
  });
});

describe("isExternal", () => {
  it("covers frameworks as well as third-party code", () => {
    expect(isExternal("external:react")).toBe(true);
    expect(isExternal("framework:typo3-core")).toBe(true);
    expect(isExternal("src/main.py")).toBe(false);
    expect(isExternal("pkg:packages/core")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The shared fixture. Its Python half lives in tests/unit/test_ids.py and reads
// the same file, so a rule that holds on one side and not the other fails a
// build instead of surviving as a live bug in whichever surface is quieter.
// ---------------------------------------------------------------------------

const FIXTURE = fixture as {
  cases: {
    raw: string;
    kind: string;
    file_path: string | null;
    symbol_name: string | null;
    is_external: boolean;
  }[];
  display_labels: { raw: string; label: string }[];
};

describe("the shared fixture holds in TypeScript", () => {
  it.each(FIXTURE.cases)("$raw", (item) => {
    expect(nodeKind(item.raw)).toBe(item.kind);
    expect(filePathOf(item.raw)).toBe(item.file_path);
    expect(symbolNameOf(item.raw)).toBe(item.symbol_name);
    expect(isExternal(item.raw)).toBe(item.is_external);
  });

  it.each(FIXTURE.display_labels)("label of $raw", (item) => {
    expect(displayLabel(item.raw)).toBe(item.label);
  });
});

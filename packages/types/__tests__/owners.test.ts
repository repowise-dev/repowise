import { describe, expect, it } from "vitest";

import { knowledgeMatrixPath, type KnowledgeMatrixFile } from "../src/owners.js";

describe("knowledgeMatrixPath", () => {
  const row = (dir: number, name: string): KnowledgeMatrixFile => [dir, name, 0, null, false, 0, null, [], false];

  it("joins the row's directory and file name", () => {
    expect(knowledgeMatrixPath(["", "src/core"], row(1, "a.py"))).toBe("src/core/a.py");
  });

  it("leaves a root file bare", () => {
    expect(knowledgeMatrixPath(["", "src"], row(0, "setup.py"))).toBe("setup.py");
  });
});

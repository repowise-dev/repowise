import { helper } from "../helper";

// Phase 1's ``**/*.test.ts`` never-flag must exempt this file. Without it
// the file looks like a top-level orphan (vitest discovers it via filename
// convention, not via static import).
describe("helper", () => {
  it("returns world", () => {
    expect(helper()).toBe("world");
  });
});

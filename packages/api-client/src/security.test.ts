import { describe, expectTypeOf, it } from "vitest";
import type * as Wire from "@repowise-dev/types/security";

import type { SecurityFinding, SecuritySummary } from "./security";

describe("security client types", () => {
  it("are the types package's wire shapes, new fields included", () => {
    expectTypeOf<SecurityFinding>().toEqualTypeOf<Wire.SecurityFinding>();
    expectTypeOf<SecuritySummary>().toEqualTypeOf<Wire.SecuritySummary>();
    expectTypeOf<SecuritySummary["class_counts"]>().not.toBeNever();
  });
});

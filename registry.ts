import { collateralEndpoint as collateralEndpoint } from "./collateral";
import { pos as pos } from "./pos";
// Use the aliased imports to create graph edges
const collateral = collateralEndpoint;
const posValue = pos;
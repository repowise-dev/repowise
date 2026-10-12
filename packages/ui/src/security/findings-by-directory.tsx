/**
 * @deprecated Use `SeverityDirectoryMatrix` from `./severity-directory-matrix`,
 * which takes the same `findings` and `depth` props. This alias keeps the old
 * import path working for one release and is then removed.
 */
import {
  SeverityDirectoryMatrix,
  type SeverityDirectoryMatrixProps,
} from "./severity-directory-matrix";

/** @deprecated Use `SeverityDirectoryMatrixProps`. */
export type FindingsByDirectoryProps = SeverityDirectoryMatrixProps;

/** @deprecated Use `SeverityDirectoryMatrix`. */
export const FindingsByDirectory = SeverityDirectoryMatrix;

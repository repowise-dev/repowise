// Old import path for one release, then removed. `SeverityDirectoryMatrix`
// in `./severity-directory-matrix` takes the same `findings` and `depth` props.
import {
  SeverityDirectoryMatrix,
  type SeverityDirectoryMatrixProps,
} from "./severity-directory-matrix";

/** @deprecated Use `SeverityDirectoryMatrixProps`. */
export type FindingsByDirectoryProps = SeverityDirectoryMatrixProps;

/**
 * @deprecated Use `SeverityDirectoryMatrix`. This renders it: a plain count
 * table of directory by severity, 14 rows by default, with no card or title.
 */
export const FindingsByDirectory = SeverityDirectoryMatrix;

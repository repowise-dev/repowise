/**
 * Shared TypeScript interfaces and types for the sample API client package.
 *
 * These types define the domain model for a simple arithmetic API service.
 * They are consumed by client.ts and utils.ts.
 */

/** Supported arithmetic operations. */
export type OperationType = "add" | "subtract" | "multiply" | "divide";

/** A request to perform an arithmetic operation. */
export interface CalculationRequest {
  /** The operation to perform. */
  operation: OperationType;
  /** The first operand. */
  x: number;
  /** The second operand. */
  y: number;
}

/** The result of a successful arithmetic calculation. */
export interface CalculationResponse {
  /** The operation that was performed. */
  operation: OperationType;
  /** Input operands in order. */
  operands: [number, number];
  /** The computed result. */
  result: number;
  /** ISO 8601 timestamp of when the calculation was performed. */
  timestamp: string;
}

/** Error response returned by the API on failure. */
export interface ApiError {
  /** Machine-readable error code. */
  code: string;
  /** Human-readable error message. */
  message: string;
  /** HTTP status code. */
  status: number;
}

/** Configuration options for the ApiClient. */
export interface ApiClientConfig {
  /** Base URL of the arithmetic API service. */
  baseUrl: string;
  /** Optional request timeout in milliseconds. Defaults to 10000. */
  timeoutMs?: number;
  /** Optional API key for authenticated endpoints. */
  apiKey?: string;
}

/** A paginated list of past calculations. */
export interface CalculationHistory {
  /** The list of calculation results. */
  entries: CalculationResponse[];
  /** Total number of entries available. */
  total: number;
  /** Number of entries returned per page. */
  pageSize: number;
  /** Zero-based current page index. */
  page: number;
}

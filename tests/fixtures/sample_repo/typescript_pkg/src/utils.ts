/**
 * Utility functions for the sample TypeScript API client.
 *
 * Provides request building, response validation, and formatting helpers.
 * Consumed by client.ts — kept separate to demonstrate cross-file dependency
 * detection in repowise's graph builder.
 */

import type { ApiError, CalculationRequest, OperationType } from "./types";

/** Valid operation strings for runtime validation. */
const VALID_OPERATIONS: ReadonlySet<OperationType> = new Set([
  "add",
  "subtract",
  "multiply",
  "divide",
]);

/**
 * Validate a CalculationRequest before sending it to the API.
 *
 * @param request - The request object to validate.
 * @returns An array of validation error messages. Empty array means valid.
 *
 * @example
 * const errors = validateRequest({ operation: "add", x: 1, y: 2 });
 * if (errors.length > 0) console.error(errors);
 */
export function validateRequest(request: CalculationRequest): string[] {
  const errors: string[] = [];

  if (!VALID_OPERATIONS.has(request.operation)) {
    errors.push(
      `Invalid operation: "${request.operation}". ` +
        `Must be one of: ${[...VALID_OPERATIONS].join(", ")}`
    );
  }

  if (!Number.isFinite(request.x)) {
    errors.push(`x must be a finite number, got: ${request.x}`);
  }

  if (!Number.isFinite(request.y)) {
    errors.push(`y must be a finite number, got: ${request.y}`);
  }

  if (request.operation === "divide" && request.y === 0) {
    errors.push("Division by zero: y must not be 0 when operation is 'divide'");
  }

  return errors;
}

/**
 * Build HTTP request headers for the API.
 *
 * @param apiKey - Optional API key to include in the Authorization header.
 * @returns A Headers-compatible object.
 */
export function buildHeaders(apiKey?: string): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };

  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
  }

  return headers;
}

/**
 * Parse an API error response into a typed ApiError.
 *
 * @param status  - HTTP status code from the response.
 * @param body    - Raw response body (may be any shape — we normalize it).
 * @returns A normalized ApiError object.
 */
export function parseApiError(status: number, body: unknown): ApiError {
  if (
    typeof body === "object" &&
    body !== null &&
    "code" in body &&
    "message" in body
  ) {
    return {
      code: String((body as Record<string, unknown>).code),
      message: String((body as Record<string, unknown>).message),
      status,
    };
  }

  return {
    code: "UNKNOWN_ERROR",
    message: typeof body === "string" ? body : "An unknown error occurred",
    status,
  };
}

/**
 * Format a number for display, stripping unnecessary trailing zeros.
 *
 * @param value        - The number to format.
 * @param maxDecimals  - Maximum decimal places to show. Defaults to 6.
 * @returns A clean string representation.
 *
 * @example
 * formatNumber(3.0)       // "3"
 * formatNumber(3.14)      // "3.14"
 * formatNumber(1/3, 4)    // "0.3333"
 */
export function formatNumber(value: number, maxDecimals = 6): string {
  const formatted = value.toFixed(maxDecimals);
  return formatted.includes(".")
    ? formatted.replace(/\.?0+$/, "")
    : formatted;
}

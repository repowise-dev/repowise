/**
 * HTTP API client for the sample arithmetic service.
 *
 * Wraps the arithmetic API with typed methods, timeout handling,
 * and structured error reporting. Consumes types from ./types and
 * helpers from ./utils.
 *
 * @example
 * const client = new ApiClient({ baseUrl: "https://api.example.com" });
 * const result = await client.calculate({ operation: "add", x: 2, y: 3 });
 * console.log(result.result); // 5
 */

import type {
  ApiClientConfig,
  ApiError,
  CalculationHistory,
  CalculationRequest,
  CalculationResponse,
} from "./types";
import { buildHeaders, parseApiError, validateRequest } from "./utils";

/** Error thrown when the API returns a non-2xx response. */
export class ApiClientError extends Error {
  public readonly apiError: ApiError;

  constructor(apiError: ApiError) {
    super(`API error ${apiError.status}: ${apiError.message}`);
    this.name = "ApiClientError";
    this.apiError = apiError;
  }
}

/** Error thrown when request validation fails before sending. */
export class ValidationError extends Error {
  public readonly validationErrors: string[];

  constructor(errors: string[]) {
    super(`Validation failed: ${errors.join("; ")}`);
    this.name = "ValidationError";
    this.validationErrors = errors;
  }
}

/** Default timeout for all requests (10 seconds). */
const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * Typed HTTP client for the arithmetic API service.
 *
 * All methods validate inputs before sending and return typed responses.
 * Network errors and API errors are surfaced as typed exceptions.
 */
export class ApiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly apiKey: string | undefined;

  constructor(config: ApiClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/$/, ""); // strip trailing slash
    this.timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.apiKey = config.apiKey;
  }

  /**
   * Perform a single arithmetic calculation.
   *
   * @param request - The calculation to perform.
   * @returns The result from the API.
   * @throws {ValidationError} If the request fails client-side validation.
   * @throws {ApiClientError} If the API returns a non-2xx response.
   */
  async calculate(request: CalculationRequest): Promise<CalculationResponse> {
    const errors = validateRequest(request);
    if (errors.length > 0) {
      throw new ValidationError(errors);
    }

    const response = await this.post<CalculationResponse>(
      "/calculations",
      request
    );
    return response;
  }

  /**
   * Retrieve the calculation history, paginated.
   *
   * @param page     - Zero-based page index. Defaults to 0.
   * @param pageSize - Number of entries per page. Defaults to 20.
   * @returns A paginated CalculationHistory response.
   * @throws {ApiClientError} If the API returns a non-2xx response.
   */
  async getHistory(
    page = 0,
    pageSize = 20
  ): Promise<CalculationHistory> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    return this.get<CalculationHistory>(`/calculations/history?${params}`);
  }

  /**
   * Check the API server health.
   *
   * @returns True if the server responded with 200 OK.
   */
  async healthCheck(): Promise<boolean> {
    try {
      await this.get<{ status: string }>("/health");
      return true;
    } catch {
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // Private HTTP helpers
  // ---------------------------------------------------------------------------

  private async get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: buildHeaders(this.apiKey),
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      const data: unknown = await response.json();

      if (!response.ok) {
        throw new ApiClientError(parseApiError(response.status, data));
      }

      return data as T;
    } finally {
      clearTimeout(timer);
    }
  }
}

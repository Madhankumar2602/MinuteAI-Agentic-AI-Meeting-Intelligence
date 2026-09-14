/**
 * The single place the frontend talks HTTP to the MinuteAI API.
 *
 * Every backend error uses one envelope:
 *   {"error": {"code", "message", "request_id", "details"}}
 * This client turns that into an `ApiError`, so pages can branch on a stable
 * machine-readable `code` and show the human `message`, and a user reporting a
 * problem can quote the `requestId` that appears in the server logs.
 */

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, requestId: string | null = null, details: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

interface ClientConfig {
  getToken: () => string | null;
  onUnauthorized: () => void;
}

let config: ClientConfig = {
  getToken: () => null,
  onUnauthorized: () => undefined,
};

/** Called once by the auth provider to connect token storage to the client. */
export function configureApiClient(next: ClientConfig): void {
  config = next;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  form?: Record<string, string>;
  signal?: AbortSignal;
  /** Attach the bearer token (default true). */
  auth?: boolean;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", json, form, signal, auth = true } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;

  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(form).toString();
  }

  const token = auth ? config.getToken() : null;
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { method, headers, body, signal });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", "Cannot reach the MinuteAI server. Check that it is running.");
  }

  if (response.status === 204) return undefined as T;

  const payload: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const error = parseError(response, payload);
    // An expired or revoked session: sign out globally rather than letting
    // every page render its own confusing failure.
    if (response.status === 401 && auth && token) config.onUnauthorized();
    throw error;
  }
  return payload as T;
}

function parseError(response: Response, payload: unknown): ApiError {
  const envelope = (payload as { error?: Record<string, unknown> } | null)?.error;
  const requestId = response.headers.get("X-Request-ID");
  if (envelope && typeof envelope.message === "string") {
    return new ApiError(
      response.status,
      typeof envelope.code === "string" ? envelope.code : "error",
      envelope.message,
      typeof envelope.request_id === "string" ? envelope.request_id : requestId,
      envelope.details ?? null,
    );
  }
  return new ApiError(response.status, "http_error", `Request failed (HTTP ${response.status}).`, requestId);
}

/** A message safe to show a user for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "validation_error" && Array.isArray(error.details) && error.details.length > 0) {
      const first = error.details[0] as { field?: string; message?: string };
      const field = first.field?.split(".").pop();
      return field ? `${field}: ${first.message}` : (first.message ?? error.message);
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

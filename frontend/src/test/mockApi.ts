/**
 * A tiny fetch mock for component tests.
 *
 * Routes are matched on "METHOD /path" (query string ignored unless the route
 * includes one). Every request is recorded so tests can assert on what the UI
 * actually sent. Unmatched requests fail loudly rather than hanging.
 */
import { vi } from "vitest";

type Handler = (request: { url: URL; method: string; body: unknown; headers: Headers }) => {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
};

export interface RecordedRequest {
  method: string;
  path: string;
  search: string;
  body: unknown;
  authorization: string | null;
}

export function mockApi(routes: Record<string, Handler | object>) {
  const requests: RecordedRequest[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    let body: unknown = init.body ?? null;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch {
        body = Object.fromEntries(new URLSearchParams(body as string));
      }
    }
    requests.push({ method, path: url.pathname, search: url.search, body, authorization: headers.get("Authorization") });

    const key = [`${method} ${url.pathname}${url.search}`, `${method} ${url.pathname}`].find((k) => k in routes);
    if (!key) {
      throw new Error(`Unmocked request: ${method} ${url.pathname}${url.search}`);
    }
    const route = routes[key]!;
    // A route is a handler function, a full mock response (from apiError), or
    // a plain object used as a 200 JSON body.
    const result: { status?: number; body?: unknown; headers?: Record<string, string> } =
      typeof route === "function"
        ? (route as Handler)({ url, method, body, headers })
        : isMockResponse(route)
          ? route
          : { body: route };
    const status = result.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(result.body ?? {}), {
      status,
      headers: { "Content-Type": "application/json", ...(result.headers ?? {}) },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return { requests, fetchMock };
}

const MOCK_RESPONSE = Symbol("mockResponse");

function isMockResponse(value: object): value is { status: number; body: unknown } {
  return MOCK_RESPONSE in value;
}

/** An error response in the API's envelope. Usable as a route or a handler result. */
export const apiError = (status: number, code: string, message: string, requestId = "req-123") => ({
  [MOCK_RESPONSE]: true,
  status,
  body: { error: { code, message, request_id: requestId } },
});

// ---------------------------------------------------------------------------
// Fixtures shaped exactly like the generated API types
// ---------------------------------------------------------------------------

export const USER = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "priya@example.com",
  full_name: "Priya Sharma",
  is_active: true,
  created_at: "2026-09-01T09:00:00Z",
};

export const MEETING = {
  id: "22222222-2222-2222-2222-222222222222",
  owner_id: USER.id,
  title: "Platform sync",
  description: null,
  meeting_date: "2026-09-10T10:00:00Z",
  source_type: "text" as const,
  status: "completed" as const,
  created_at: "2026-09-10T11:00:00Z",
  updated_at: "2026-09-10T11:00:00Z",
};

export function actionItem(overrides: Record<string, unknown> = {}) {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    meeting_id: MEETING.id,
    position: 0,
    task: "Prepare the production migration runbook",
    owner_name: "Karthik",
    owner_participant_id: "44444444-4444-4444-4444-444444444444",
    deadline: "2026-09-16",
    deadline_text: "by next Wednesday",
    priority: null,
    status: "pending",
    evidence_quote: "I'll have the runbook ready by next Wednesday.",
    evidence_verified: true,
    created_at: "2026-09-10T11:00:00Z",
    updated_at: "2026-09-10T11:00:00Z",
    meeting_title: null,
    is_overdue: false,
    ...overrides,
  };
}

export function job(overrides: Record<string, unknown> = {}) {
  return {
    job_id: "55555555-5555-5555-5555-555555555555",
    meeting_id: MEETING.id,
    status: "COMPLETED",
    force: false,
    attempts: 1,
    max_attempts: 3,
    created_at: "2026-09-10T11:00:00Z",
    updated_at: "2026-09-10T11:00:20Z",
    started_at: "2026-09-10T11:00:01Z",
    finished_at: "2026-09-10T11:00:20Z",
    next_attempt_at: null,
    error: null,
    result: { cached: false, transcribed: false, decisions: 1, action_items: 1, participants: 2, warnings: [] },
    events: [
      { at: "2026-09-10T11:00:00Z", type: "queued", detail: { force: false } },
      { at: "2026-09-10T11:00:01Z", type: "started", detail: { attempt: 1, worker_id: "w" } },
      { at: "2026-09-10T11:00:20Z", type: "completed", detail: { cached: false } },
    ],
    ...overrides,
  };
}

export function intelligence(overrides: Record<string, unknown> = {}) {
  return {
    meeting_id: MEETING.id,
    status: "completed",
    cached: true,
    warnings: [],
    summary: {
      summary_text: "The team agreed to migrate production to PostgreSQL 16.",
      key_points: ["Staging is on PostgreSQL 16"],
      provider: "gemini",
      model: "gemini-3.6-flash",
      prompt_version: "extract-v1",
      input_tokens: 882,
      output_tokens: 480,
      latency_ms: 19000,
      created_at: "2026-09-10T11:00:20Z",
      is_stale: false,
    },
    participants: [
      { id: "44444444-4444-4444-4444-444444444444", display_name: "Karthik", email: null, user_id: null },
      { id: "66666666-6666-6666-6666-666666666666", display_name: "Priya", email: null, user_id: null },
    ],
    decisions: [
      {
        id: "77777777-7777-7777-7777-777777777777",
        meeting_id: MEETING.id,
        position: 0,
        decision_text: "Migrate production to PostgreSQL 16 on Sunday.",
        context: null,
        evidence_quote: "We will migrate the production database to PostgreSQL 16",
        evidence_verified: false,
        status: "open",
        created_at: "2026-09-10T11:00:20Z",
        updated_at: "2026-09-10T11:00:20Z",
      },
    ],
    action_items: [actionItem()],
    ...overrides,
  };
}

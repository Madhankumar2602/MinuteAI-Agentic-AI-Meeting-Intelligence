/**
 * Page-level tests: the real routes, providers, and data layer render against a
 * mocked fetch. They check what a user sees and what is actually sent.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { session } from "../auth/session";
import { MEETING, USER, actionItem, apiError, intelligence, job, mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const DASHBOARD = {
  meetings: { total: 2, by_status: { created: 0, queued: 0, processing: 0, completed: 2, failed: 0 } },
  action_items: { open: 3, overdue: 1, due_soon: 1, done: 4 },
  recent_meetings: [MEETING],
  attention: [actionItem({ task: "Fix clock sync", is_overdue: true, deadline: "2026-09-01", meeting_title: "Platform sync" })],
};

const TRANSCRIPT = {
  id: "t", meeting_id: MEETING.id, content: "Karthik: I'll have the runbook ready by next Wednesday.",
  content_sha256: "x", char_count: 50, word_count: 9, language: "en", source: "manual",
  media_id: null, transcription_model: null, duration_seconds: null,
  created_at: MEETING.created_at, updated_at: MEETING.created_at,
};

function detailRoutes(overrides: Record<string, object> = {}) {
  return {
    "GET /api/v1/auth/me": USER,
    "GET /api/v1/dashboard": DASHBOARD,
    [`GET /api/v1/meetings/${MEETING.id}`]: MEETING,
    [`GET /api/v1/meetings/${MEETING.id}/jobs`]: [job()],
    [`GET /api/v1/meetings/${MEETING.id}/intelligence`]: intelligence(),
    [`GET /api/v1/meetings/${MEETING.id}/transcript`]: TRANSCRIPT,
    [`GET /api/v1/meetings/${MEETING.id}/media`]: apiError(404, "not_found", "No recording."),
    ...overrides,
  };
}

describe("authentication", () => {
  it("redirects to sign-in when not signed in", async () => {
    mockApi({});
    renderApp("/meetings", { signedIn: false });
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });

  it("signs in, stores the token for the tab, and shows the dashboard", async () => {
    const { requests } = mockApi({
      "POST /api/v1/auth/login": { access_token: "fresh-token", token_type: "bearer", expires_at: "2026-09-14T12:00:00Z" },
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": DASHBOARD,
    });
    renderApp("/", { signedIn: false });
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "priya@example.com");
    await user.type(screen.getByLabelText("Password"), "a-good-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: /, Priya$/ })).toBeInTheDocument();
    expect(requests.find((r) => r.path === "/api/v1/auth/login")!.body).toEqual({ username: "priya@example.com", password: "a-good-password" });
    expect(sessionStorage.getItem("minuteai.token")).toBe("fresh-token");
    expect(requests.find((r) => r.path === "/api/v1/dashboard")!.authorization).toBe("Bearer fresh-token");
  });

  it("shows the server's message on a failed sign-in", async () => {
    mockApi({ "POST /api/v1/auth/login": apiError(401, "unauthorized", "Incorrect email or password.") });
    renderApp("/login", { signedIn: false });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Email"), "priya@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
  });

  it("signs out when a stored token has expired", async () => {
    mockApi({ "GET /api/v1/auth/me": apiError(401, "unauthorized", "Token has expired.") });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
    expect(session.getToken()).toBeNull();
  });

  it("validates password length before registering", async () => {
    const { requests } = mockApi({});
    renderApp("/register", { signedIn: false });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Full name"), "Priya");
    await user.type(screen.getByLabelText("Email"), "p@example.com");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: /Create account/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("at least 8 characters");
    expect(requests).toHaveLength(0);
  });
});

describe("dashboard", () => {
  it("shows counts, overdue items with their meeting, and recent meetings", async () => {
    mockApi({ "GET /api/v1/auth/me": USER, "GET /api/v1/dashboard": DASHBOARD });
    renderApp("/");

    const attention = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(attention).getByText("Fix clock sync")).toBeInTheDocument();
    expect(within(attention).getByText(/days overdue/)).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: /Platform sync/ })).toHaveAttribute("href", `/meetings/${MEETING.id}`);
    expect(screen.getByRole("group", { name: "Overdue" })).toHaveTextContent("1");
    expect(screen.getByText("1 action item is overdue.", { exact: false })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Recent meetings" })).getByText("Ready")).toBeInTheDocument();
    // Overdue count is surfaced in the navigation too.
    expect(screen.getByLabelText("1 overdue")).toBeInTheDocument();
  });

  it("guides a new user to create their first meeting", async () => {
    mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": { ...DASHBOARD, meetings: { total: 0, by_status: {} }, action_items: { open: 0, overdue: 0, due_soon: 0, done: 0 }, recent_meetings: [], attention: [] },
    });
    renderApp("/");
    expect(await screen.findByRole("link", { name: /Create your first meeting/ })).toHaveAttribute("href", "/meetings/new");
  });

  it("does not offer unbuilt features as working pages", async () => {
    mockApi({ "GET /api/v1/auth/me": USER, "GET /api/v1/dashboard": DASHBOARD });
    renderApp("/");
    const upcoming = await screen.findByText("Ask your meetings");
    expect(upcoming.closest("[aria-disabled]")).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByRole("link", { name: /Ask your meetings/ })).not.toBeInTheDocument();
  });

  it("marks an action item done with one click and confirms with a toast", async () => {
    const { requests } = mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": DASHBOARD,
      [`PATCH /api/v1/action-items/${actionItem().id}`]: actionItem({ status: "done" }),
    });
    renderApp("/");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: 'Mark "Fix clock sync" as done' }));

    expect(await screen.findByText("Marked as done")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PATCH")!.body).toEqual({ status: "done" });
  });
});

describe("meetings list", () => {
  it("searches after typing pauses and shows a clear empty state", async () => {
    const { requests } = mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": DASHBOARD,
      "GET /api/v1/meetings?page=1&size=20": { items: [MEETING], total: 1, page: 1, size: 20 },
      "GET /api/v1/meetings?page=1&size=20&q=roadmap": { items: [], total: 0, page: 1, size: 20 },
    });
    renderApp("/meetings");
    const user = userEvent.setup();

    expect(await screen.findByText("Platform sync")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search meetings"), "roadmap");

    expect(await screen.findByRole("heading", { name: "No meetings match" })).toBeInTheDocument();
    // Debounced: one request for the finished word, not one per keystroke.
    expect(requests.filter((r) => r.search.includes("q=")).map((r) => r.search)).toEqual(["?page=1&size=20&q=roadmap"]);
  });
});

describe("new meeting", () => {
  it("creates the meeting, saves the transcript, queues processing, and opens it", async () => {
    const { requests } = mockApi(
      detailRoutes({
        "POST /api/v1/meetings": { ...MEETING, status: "created" },
        [`PUT /api/v1/meetings/${MEETING.id}/transcript`]: TRANSCRIPT,
        [`POST /api/v1/meetings/${MEETING.id}/process`]: { cached: false, meeting_status: "queued", job: job({ status: "QUEUED" }) },
        [`GET /api/v1/meetings/${MEETING.id}`]: { ...MEETING, status: "queued" },
        [`GET /api/v1/meetings/${MEETING.id}/jobs`]: [job({ status: "QUEUED", attempts: 0, result: null, events: [{ at: "2026-09-10T11:00:00Z", type: "queued", detail: {} }] })],
        [`GET /api/v1/meetings/${MEETING.id}/intelligence`]: intelligence({ summary: null, decisions: [], action_items: [], participants: [], status: "queued" }),
      }),
    );
    renderApp("/meetings/new");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Title"), "Platform sync");
    await user.type(screen.getByLabelText("Transcript"), "Priya: Karthik, please prepare the runbook by Friday.");
    await user.click(screen.getByRole("button", { name: /Create and analyse/ }));

    expect(await screen.findByRole("heading", { name: "Processing" })).toBeInTheDocument();
    expect(await screen.findByText("Meeting created — analysis started")).toBeInTheDocument();
    const order = requests.filter((r) => r.method !== "GET").map((r) => `${r.method} ${r.path}`);
    expect(order).toEqual([
      "POST /api/v1/meetings",
      `PUT /api/v1/meetings/${MEETING.id}/transcript`,
      `POST /api/v1/meetings/${MEETING.id}/process`,
    ]);
    expect(requests.find((r) => r.method === "POST" && r.path === "/api/v1/meetings")!.body).toMatchObject({ title: "Platform sync", source_type: "text" });
  });

  it("requires a title and enough transcript text before sending anything", async () => {
    const { requests } = mockApi({ "GET /api/v1/auth/me": USER, "GET /api/v1/dashboard": DASHBOARD });
    renderApp("/meetings/new");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Create and analyse/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Give the meeting a title.");
    expect(screen.getByLabelText("Title")).toHaveAttribute("aria-invalid", "true");

    await user.type(screen.getByLabelText("Title"), "Sync");
    await user.type(screen.getByLabelText("Transcript"), "too short");
    await user.click(screen.getByRole("button", { name: /Create and analyse/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("at least 20 characters");
    expect(requests.filter((r) => r.method === "POST")).toHaveLength(0);
  });

  it("rejects an unsupported file before uploading anything", async () => {
    const { requests } = mockApi({ "GET /api/v1/auth/me": USER, "GET /api/v1/dashboard": DASHBOARD });
    renderApp("/meetings/new");
    const user = userEvent.setup({ applyAccept: false });

    await user.click(await screen.findByRole("button", { name: /Upload recording/ }));
    await user.upload(screen.getByTestId("recording-input"), new File(["%PDF"], "minutes.pdf", { type: "application/pdf" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("not a supported audio or video file");
    expect(requests.filter((r) => r.method === "POST")).toHaveLength(0);
  });
});

describe("meeting detail", () => {
  it("shows the summary on the overview and results on their tabs", async () => {
    mockApi(detailRoutes());
    renderApp(`/meetings/${MEETING.id}`);
    const user = userEvent.setup();

    expect(await screen.findByText("The team agreed to migrate production to PostgreSQL 16.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Participants" })).getByText("Karthik")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Action items/ }));
    const actions = screen.getByRole("region", { name: "Action items" });
    expect(within(actions).getByText("Prepare the production migration runbook")).toBeInTheDocument();
    expect(within(actions).getByText("Evidence verified")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Decisions/ }));
    expect(within(screen.getByRole("region", { name: "Decisions" })).getByText("Evidence not found")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Transcript/ }));
    expect(screen.getByLabelText("Transcript text")).toHaveTextContent("Karthik: I'll have the runbook ready");
  });

  it("updates an action item's status", async () => {
    const { requests } = mockApi(detailRoutes({ [`PATCH /api/v1/action-items/${actionItem().id}`]: actionItem({ status: "in_progress" }) }));
    renderApp(`/meetings/${MEETING.id}`);
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByLabelText("Status of Prepare the production migration runbook"), "in_progress");

    await waitFor(() => expect(requests.some((r) => r.method === "PATCH")).toBe(true));
    expect(requests.find((r) => r.method === "PATCH")!.body).toEqual({ status: "in_progress" });
  });

  it("shows step-by-step progress, including a scheduled retry, while processing", async () => {
    mockApi(
      detailRoutes({
        [`GET /api/v1/meetings/${MEETING.id}`]: { ...MEETING, status: "queued" },
        [`GET /api/v1/meetings/${MEETING.id}/jobs`]: [
          job({
            status: "QUEUED",
            attempts: 1,
            result: null,
            error: { code: "llm_rate_limited", message: "The AI provider's rate limit was reached." },
            next_attempt_at: "2026-09-10T11:01:00Z",
            events: [
              { at: "2026-09-10T11:00:00Z", type: "queued", detail: {} },
              { at: "2026-09-10T11:00:01Z", type: "transcription_started", detail: {} },
              { at: "2026-09-10T11:00:30Z", type: "retry_scheduled", detail: { error_code: "llm_rate_limited" } },
            ],
          }),
        ],
        [`GET /api/v1/meetings/${MEETING.id}/intelligence`]: intelligence({ summary: null, decisions: [], action_items: [], participants: [] }),
      }),
    );
    renderApp(`/meetings/${MEETING.id}`);

    const processing = await screen.findByRole("region", { name: "Processing" });
    const steps = within(processing).getByRole("list", { name: "Processing steps" });
    expect(within(steps).getByText("Transcribing").closest("li")).toHaveAttribute("aria-current", "step");
    expect(within(processing).getByText(/Retrying automatically/)).toBeInTheDocument();
    // Destructive and duplicate actions are unavailable while a job is active.
    expect(screen.queryByRole("button", { name: /Process meeting|Re-run AI/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete/ })).toBeDisabled();
  });

  it("shows a failed job's reason and offers to process again", async () => {
    mockApi(
      detailRoutes({
        [`GET /api/v1/meetings/${MEETING.id}`]: { ...MEETING, status: "failed" },
        [`GET /api/v1/meetings/${MEETING.id}/jobs`]: [
          job({ status: "FAILED", result: null, error: { code: "no_speech_detected", message: "No usable speech was found in the recording." } }),
        ],
        [`GET /api/v1/meetings/${MEETING.id}/intelligence`]: intelligence({ summary: null, decisions: [], action_items: [], participants: [] }),
      }),
    );
    renderApp(`/meetings/${MEETING.id}`);

    expect(await screen.findByText("No usable speech was found in the recording.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Process meeting/ })).toBeEnabled();
  });

  it("asks for confirmation before re-running, and reports when results were already current", async () => {
    const { requests } = mockApi(
      detailRoutes({ [`POST /api/v1/meetings/${MEETING.id}/process?force=true`]: { cached: true, meeting_status: "completed", job: null } }),
    );
    renderApp(`/meetings/${MEETING.id}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Re-run AI/ }));
    const dialog = await screen.findByRole("alertdialog", { name: "Re-run the AI analysis?" });
    await user.click(within(dialog).getByRole("button", { name: "Re-run analysis" }));

    expect(await screen.findByText("Already up to date")).toBeInTheDocument();
    expect(requests.some((r) => r.search === "?force=true")).toBe(true);
  });

  it("does not delete when the confirmation is cancelled, and deletes when confirmed", async () => {
    const { requests } = mockApi(
      detailRoutes({
        [`DELETE /api/v1/meetings/${MEETING.id}`]: { status: 204 },
        "GET /api/v1/meetings?page=1&size=20": { items: [], total: 0, page: 1, size: 20 },
      }),
    );
    renderApp(`/meetings/${MEETING.id}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Delete/ }));
    let dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus(); // safe default
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: /Delete/ }));
    dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete meeting" }));

    expect(await screen.findByText("Meeting deleted")).toBeInTheDocument();
    expect(requests.some((r) => r.method === "DELETE")).toBe(true);
  });

  it("shows a not-found page for a meeting the user cannot access", async () => {
    mockApi({ "GET /api/v1/auth/me": USER, "GET /api/v1/dashboard": DASHBOARD, [`GET /api/v1/meetings/${MEETING.id}`]: apiError(404, "not_found", "Meeting not found.") });
    renderApp(`/meetings/${MEETING.id}`);
    expect(await screen.findByRole("heading", { name: "Meeting not found" })).toBeInTheDocument();
  });
});

describe("action items", () => {
  it("groups open items by urgency and links each to its meeting", async () => {
    mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": DASHBOARD,
      "GET /api/v1/action-items?size=100": {
        items: [
          actionItem({ id: "a1", task: "Late task", is_overdue: true, deadline: "2020-01-01", meeting_title: "Platform sync" }),
          actionItem({ id: "a2", task: "Someday task", deadline: null, meeting_title: "Platform sync" }),
          actionItem({ id: "a3", task: "Finished task", status: "done", meeting_title: "Platform sync" }),
        ],
        total: 3, page: 1, size: 100,
      },
    });
    renderApp("/action-items");

    const overdue = await screen.findByRole("region", { name: "Overdue" });
    expect(within(overdue).getByText("Late task")).toBeInTheDocument();
    expect(within(overdue).getByRole("link", { name: /Platform sync/ })).toHaveAttribute("href", `/meetings/${MEETING.id}`);
    expect(within(screen.getByRole("region", { name: "No deadline" })).getByText("Someday task")).toBeInTheDocument();
    // "Open" hides completed work.
    expect(screen.queryByText("Finished task")).not.toBeInTheDocument();
  });

  it("shows completed items under the Done filter", async () => {
    const { requests } = mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": DASHBOARD,
      "GET /api/v1/action-items?size=100": { items: [], total: 0, page: 1, size: 100 },
      "GET /api/v1/action-items?status=done&size=100": { items: [actionItem({ task: "Finished task", status: "done" })], total: 1, page: 1, size: 100 },
    });
    renderApp("/action-items");
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "You're all caught up" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Done" }));

    expect(await screen.findByText("Finished task")).toBeInTheDocument();
    expect(requests.some((r) => r.search === "?status=done&size=100")).toBe(true);
  });
});

describe("theme and resilience", () => {
  it("cycles the theme and applies it to the document", async () => {
    mockApi({ "GET /api/v1/auth/me": USER, "GET /api/v1/dashboard": DASHBOARD });
    renderApp("/");
    const user = userEvent.setup();

    const toggle = (await screen.findAllByRole("button", { name: /theme — switch to/ }))[0]!;
    const before = document.documentElement.dataset.theme;
    await user.click(toggle);
    await user.click(screen.getAllByRole("button", { name: /theme — switch to/ })[0]!);
    const seen = new Set([before, document.documentElement.dataset.theme]);
    expect([...seen].every((t) => t === "light" || t === "dark")).toBe(true);
    expect(localStorage.getItem("minuteai.theme")).not.toBeNull();
  });

  it("renders an unknown status from a newer API instead of crashing", async () => {
    mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": { ...DASHBOARD, recent_meetings: [{ ...MEETING, status: "archived" }] },
    });
    renderApp("/");
    expect(await screen.findByText("archived")).toBeInTheDocument();
  });
});

describe("semantic search", () => {
  const PASSAGE = "Priya: Users are still getting logged out after about ten minutes.\nMeera: The clock on one API server has drifted.";
  const hit = (overrides: Record<string, unknown> = {}) => ({
    chunk_id: "c1", meeting_id: MEETING.id, meeting_title: "Platform sync", meeting_date: MEETING.meeting_date,
    chunk_index: 1, content: PASSAGE, char_start: 120, char_end: 240, score: 0.56, ...overrides,
  });

  it("searches by meaning after typing pauses and links each passage to its place in the transcript", async () => {
    const { requests } = mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": DASHBOARD,
      "GET /api/v1/search": ({ url }) => ({
        body: { query: url.searchParams.get("q"), model: "minilm", results: [hit(), hit({ chunk_id: "c2", score: 0.12, content: "Arjun: Morning." })] },
      }),
    });
    renderApp("/search");
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "Search across every processed meeting" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search meeting transcripts"), "why are people signed out");

    const results = await screen.findByRole("list", { name: "Search results" });
    const [first, second] = within(results).getAllByRole("listitem");
    expect(within(first!).getByText("Strong match")).toBeInTheDocument();
    expect(within(first!).getByText("Users are still getting logged out after about ten minutes.", { exact: false })).toBeInTheDocument();
    expect(within(first!).getByRole("link", { name: /Open in transcript/ })).toHaveAttribute(
      "href", `/meetings/${MEETING.id}?tab=transcript&from=120&to=240`,
    );
    expect(within(second!).getByText("Weak match")).toBeInTheDocument();
    // Debounced: a single request for the finished question.
    expect(requests.filter((r) => r.path === "/api/v1/search").map((r) => new URLSearchParams(r.search).get("q"))).toEqual(["why are people signed out"]);
  });

  it("explains when nothing matches", async () => {
    mockApi({
      "GET /api/v1/auth/me": USER,
      "GET /api/v1/dashboard": DASHBOARD,
      "GET /api/v1/search": { query: "picnic", model: "minilm", results: [] },
    });
    renderApp("/search?q=picnic");
    expect(await screen.findByRole("heading", { name: "No matching passages" })).toBeInTheDocument();
    expect(screen.getByLabelText("Search meeting transcripts")).toHaveValue("picnic");
  });

  it("opens search with Ctrl+K from any page", async () => {
    mockApi({ "GET /api/v1/auth/me": USER, "GET /api/v1/dashboard": DASHBOARD });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: /, Priya$/ });
    await user.keyboard("{Control>}k{/Control}");
    expect(await screen.findByRole("heading", { name: "Search your meetings" })).toBeInTheDocument();
  });

  it("highlights the passage a search result points to", async () => {
    const content = "Priya: First line.\nKarthik: I'll have the runbook ready by next Wednesday.\nArjun: Last line.";
    mockApi(detailRoutes({ [`GET /api/v1/meetings/${MEETING.id}/transcript`]: { ...TRANSCRIPT, content } }));
    const start = content.indexOf("Karthik");
    renderApp(`/meetings/${MEETING.id}?tab=transcript&from=${start}&to=${start + 20}`);

    const transcript = await screen.findByLabelText("Transcript text");
    const marked = transcript.querySelectorAll("[data-highlighted]");
    expect(marked).toHaveLength(1);
    expect(marked[0]).toHaveTextContent("Karthik: I'll have the runbook ready");
  });
});

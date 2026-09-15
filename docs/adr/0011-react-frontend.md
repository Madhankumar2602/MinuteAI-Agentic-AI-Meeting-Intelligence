# ADR 0011 — React single-page frontend

- **Status:** Accepted
- **Date:** 2026-09-14
- **Milestone:** M5

## Context

The backend (M1–M4) exposes auth, meetings, transcripts, recordings, async jobs,
and AI intelligence over a JSON API. M5 adds the user interface. The main
constraints are:

- one developer, so every dependency has to earn its place;
- processing is asynchronous, so the UI needs to poll and show progress;
- recordings upload directly to object storage through a presigned POST, never
  through the API;
- the API contract changes each milestone, so hand-written client types would
  drift.

## Decision

| Concern | Choice | Why |
|---|---|---|
| Build | Vite + React 19 + TypeScript (`strict`, `noUncheckedIndexedAccess`) | Standard, fast, and the compiler catches unchecked array/record access |
| Routing | `react-router` 7 (declarative mode) | Small; tabs live in the URL (`?tab=`) so they can be linked and survive refreshes |
| Server state | TanStack Query 5 | Caching, invalidation after mutations, and **conditional polling** (`refetchInterval` only while a meeting is queued or processing) without hand-written effects |
| API types | Generated from the backend's OpenAPI schema (`npm run gen:api`) | One source of truth; a backend change that breaks the UI fails `tsc` |
| Styling | One hand-written CSS design system with custom properties, no CSS framework | Full control over the look, no build plugin, ~29 kB CSS; light and dark themes are just token swaps |
| Icons / font | `lucide-react`, self-hosted Inter (`@fontsource-variable`) | Tree-shaken icons; no third-party font request |
| Uploads | `XMLHttpRequest` to the presigned POST | `fetch` cannot report upload progress |
| Dev networking | Vite proxies `/api` and `/health` to the API on 8010 | Same origin in development, so no CORS configuration to get wrong |
| Tests | Vitest + Testing Library, rendering the **real routes** with a mocked `fetch` | Tests exercise what the user sees (roles, labels), not component internals |

### Authentication token storage

The JWT is held in memory and mirrored to `sessionStorage` so a page refresh
keeps the user signed in within that tab.

- **Not `localStorage`:** the token disappears when the tab closes, which
  narrows the window in which a stolen token is useful.
- **Not an httpOnly cookie (yet):** a cookie would hide the token from page
  scripts, but the API would then need CSRF defences and cookie-domain setup
  that depend on the deployment topology.
- **Accepted risk:** any script injected into the page could read the token.
  Mitigations are that React escapes all rendered text, the app never uses
  `dangerouslySetInnerHTML`, transcripts and model output are rendered as plain
  text, and tokens expire after 60 minutes. A `401` clears the session and returns
  the user to sign-in.

### UX principles

- **Honest progress.** The processing stepper is driven by real job events
  (`queued → transcription_started → started → completed/failed`), and retries
  show the real back-off time. Nothing is animated to look busy.
- **Evidence is visible.** Summaries, decisions, and action items carry a
  verified or unverified badge from the grounding step (ADR 0007). When a deadline
  was resolved from relative wording, the original words are shown next to it.
- **Destructive actions are confirmed** in an accessible dialog (`alertdialog`,
  Cancel focused, Escape cancels). Everything else gives a toast.
- **Nothing is faked.** Every navigation entry leads to a working page backed by
  the real API.
- Accessible names on every icon-only control, a keyboard-reachable layout, a
  theme applied before first paint (no flash), and an off-canvas sidebar below
  860 px.

## Consequences

- The UI adds a Node toolchain. `openapi-typescript` runs through `npx` because
  it currently requires TypeScript 5 and the app uses TypeScript 6.
- The generated `schema.d.ts` is committed so the frontend builds without a
  running backend; it must be regenerated after API changes.
- Polling every 2 s while a job is active is simple and adequate for one user per
  meeting. Server-sent events would reduce requests but add infrastructure; revisit
  only if needed.
- Tests mock `fetch`, so they cannot catch an API mismatch that the generated
  types also miss. The live browser check against the real API covers that gap.

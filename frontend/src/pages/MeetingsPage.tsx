import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Plus, Search, SearchX } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/endpoints";
import type { MeetingStatus } from "../api/types";
import { MeetingRow } from "../components/MeetingRow";
import { EmptyState, ErrorBanner, PageHeader, Skeleton } from "../components/ui";

const PAGE_SIZE = 20;

const FILTERS: { key: MeetingStatus | ""; label: string }[] = [
  { key: "", label: "All" },
  { key: "completed", label: "Ready" },
  { key: "processing", label: "Processing" },
  { key: "queued", label: "Queued" },
  { key: "failed", label: "Failed" },
  { key: "created", label: "Draft" },
];

/** Wait until typing pauses before searching, so each keystroke isn't a request. */
function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

export function MeetingsPage() {
  const [status, setStatus] = useState<MeetingStatus | "">("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());

  // The page number belongs to one combination of filters: changing the search
  // or status starts again at page 1, derived here rather than reset in an effect.
  const filterKey = `${status}|${q}`;
  const [pageState, setPageState] = useState({ filterKey, page: 1 });
  const page = pageState.filterKey === filterKey ? pageState.page : 1;
  const setPage = (update: (p: number) => number) => setPageState({ filterKey, page: update(page) });

  const meetings = useQuery({
    queryKey: ["meetings", { page, status, q }],
    queryFn: () => api.listMeetings({ page, size: PAGE_SIZE, status: status || undefined, q: q || undefined }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.items.some((m) => m.status === "queued" || m.status === "processing") ? 3000 : false,
  });

  const total = meetings.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = Boolean(q || status);

  return (
    <>
      <PageHeader
        title="Meetings"
        subtitle={meetings.data ? `${total} meeting${total === 1 ? "" : "s"}${filtered ? " match" : ""}` : "Everything you've captured"}
        actions={<Link className="btn btn-primary" to="/meetings/new"><Plus size={16} /> New meeting</Link>}
      />

      <div className="row-between" style={{ marginBottom: 16 }}>
        <div className="search">
          <Search size={16} />
          <label className="sr-only" htmlFor="meeting-search">Search meetings</label>
          <input
            id="meeting-search"
            className="input"
            type="search"
            placeholder="Search by title or description"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="chips" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button key={f.label} type="button" className="chip" aria-pressed={status === f.key} onClick={() => setStatus(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {meetings.isPending ? (
        <div className="card">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="meeting-row"><Skeleton height={44} /></div>
          ))}
        </div>
      ) : meetings.isError ? (
        <ErrorBanner error={meetings.error} title="Could not load meetings." />
      ) : meetings.data.items.length === 0 ? (
        <div className="card">
          {filtered ? (
            <EmptyState
              icon={SearchX}
              title="No meetings match"
              action={<button type="button" className="btn" onClick={() => { setSearch(""); setStatus(""); }}>Clear filters</button>}
            >
              Try a different search term or status.
            </EmptyState>
          ) : (
            <EmptyState icon={CalendarDays} title="No meetings yet" action={<Link className="btn btn-gradient" to="/meetings/new">Create a meeting</Link>}>
              Add a transcript or upload a recording to get started.
            </EmptyState>
          )}
        </div>
      ) : (
        <div className="card" style={{ opacity: meetings.isFetching && meetings.isPlaceholderData ? 0.6 : 1 }}>
          {meetings.data.items.map((meeting) => <MeetingRow key={meeting.id} meeting={meeting} />)}
          {totalPages > 1 && (
            <div className="card-foot row-between">
              <span className="faint small">Page {page} of {totalPages}</span>
              <div className="row">
                <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  <ChevronLeft size={15} /> Previous
                </button>
                <button type="button" className="btn btn-sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                  Next <ChevronRight size={15} />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}

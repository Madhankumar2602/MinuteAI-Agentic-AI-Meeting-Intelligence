import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ListTodo, PartyPopper } from "lucide-react";
import { useState } from "react";

import { api } from "../api/endpoints";
import { ActionItemRow } from "../components/ActionItemRow";
import { EmptyState, ErrorBanner, PageHeader, Skeleton } from "../components/ui";
import { groupByUrgency } from "../lib/groups";

type Filter = "open" | "done" | "all";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "done", label: "Done" },
  { key: "all", label: "All" },
];

// The largest page the API allows. Personal action-item lists stay well under
// it; grouping needs the full set rather than one page at a time.
const SIZE = 100;

export function ActionItemsPage() {
  const [filter, setFilter] = useState<Filter>("open");

  const items = useQuery({
    queryKey: ["action-items", filter],
    queryFn: async () => {
      if (filter === "done") return (await api.listActionItems({ status: "done", size: SIZE })).items;
      const all = (await api.listActionItems({ size: SIZE })).items;
      return filter === "open" ? all.filter((i) => i.status === "pending" || i.status === "in_progress") : all;
    },
  });

  const groups = items.data ? groupByUrgency(items.data) : [];
  const overdue = groups.find((g) => g.key === "overdue")?.items.length ?? 0;

  return (
    <>
      <PageHeader
        title="Action items"
        subtitle={
          items.data
            ? `${items.data.length} ${filter === "done" ? "completed" : filter === "open" ? "open" : "total"}${overdue ? ` · ${overdue} overdue` : ""}`
            : "Everything assigned across your meetings"
        }
        actions={
          <div className="chips" role="group" aria-label="Filter action items">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" className="chip" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
                {f.label}
              </button>
            ))}
          </div>
        }
      />

      {items.isPending ? (
        <div className="card">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="list-item"><Skeleton height={40} /></div>
          ))}
        </div>
      ) : items.isError ? (
        <ErrorBanner error={items.error} title="Could not load action items." />
      ) : groups.length === 0 ? (
        <div className="card">
          {filter === "open" ? (
            <EmptyState icon={PartyPopper} title="You're all caught up">No open action items across your meetings.</EmptyState>
          ) : filter === "done" ? (
            <EmptyState icon={CheckCircle2} title="Nothing completed yet">Tick items off and they'll appear here.</EmptyState>
          ) : (
            <EmptyState icon={ListTodo} title="No action items yet">Process a meeting and its action items appear here.</EmptyState>
          )}
        </div>
      ) : (
        <div className="card">
          {groups.map((group) => (
            <section key={group.key} aria-label={group.label}>
              <div className="group-head">
                <span className="dot" style={{ color: group.tone }} aria-hidden />
                {group.label}
                <span className="count">{group.items.length}</span>
              </div>
              <ul className="list">
                {group.items.map((item) => <ActionItemRow key={item.id} item={item} showMeeting />)}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

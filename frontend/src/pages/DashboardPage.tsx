import { useQuery } from "@tanstack/react-query";
import { AlarmClock, ArrowRight, CalendarCheck2, CalendarDays, CheckCircle2, ListTodo, Plus, Sparkles, type LucideIcon } from "lucide-react";
import { Link } from "react-router";

import { api } from "../api/endpoints";
import type { MeetingStatus } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { ActionItemRow } from "../components/ActionItemRow";
import { MeetingRow } from "../components/MeetingRow";
import { EmptyState, ErrorBanner, PageLoading } from "../components/ui";
import { greeting } from "../lib/people";

/** by_status is a dictionary in the API schema; a missing key means zero. */
const count = (byStatus: Record<string, number>, status: MeetingStatus): number => byStatus[status] ?? 0;

const dayFormatter = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });

export function DashboardPage() {
  const { user } = useAuth();
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: api.dashboard,
    // Keep counts fresh while meetings are being processed in the background.
    refetchInterval: (query) => {
      const s = query.state.data?.meetings.by_status;
      return s && (count(s, "queued") > 0 || count(s, "processing") > 0) ? 3000 : false;
    },
  });

  if (dashboard.isPending) return <PageLoading />;
  if (dashboard.isError) return <ErrorBanner error={dashboard.error} title="Could not load the dashboard." />;

  const { meetings, action_items: items, recent_meetings: recent, attention } = dashboard.data;
  const inFlight = count(meetings.by_status, "queued") + count(meetings.by_status, "processing");
  const firstName = user?.full_name.split(" ")[0];

  const headline =
    meetings.total === 0
      ? "Let's capture your first meeting."
      : items.overdue > 0
        ? `${items.overdue} action item${items.overdue === 1 ? " is" : "s are"} overdue.`
        : items.due_soon > 0
          ? `${items.due_soon} action item${items.due_soon === 1 ? " is" : "s are"} due this week.`
          : "You're all caught up.";

  return (
    <>
      <section className="hero" aria-label="Overview">
        <div className="row-between">
          <div>
            <div className="hero-date">{dayFormatter.format(new Date())}</div>
            <h1>{greeting()}{firstName ? `, ${firstName}` : ""}</h1>
            <p>
              {headline}
              {inFlight > 0 && ` ${inFlight} meeting${inFlight === 1 ? " is" : "s are"} being processed right now.`}
            </p>
          </div>
          <Link className="btn" to="/meetings/new"><Plus size={16} /> New meeting</Link>
        </div>
      </section>

      {meetings.total === 0 ? (
        <div className="card">
          <EmptyState
            icon={Sparkles}
            title="No meetings yet"
            action={<Link className="btn btn-gradient" to="/meetings/new">Create your first meeting <ArrowRight size={16} /></Link>}
          >
            Paste a transcript or upload a recording. MinuteAI extracts the summary, decisions, and action items — with evidence for each.
          </EmptyState>
        </div>
      ) : (
        <>
          <div className="stat-grid">
            <StatCard label="Meetings" value={meetings.total} tone="primary" icon={CalendarDays} foot={`${count(meetings.by_status, "completed")} ready`} />
            <StatCard label="Open action items" value={items.open} tone="warning" icon={ListTodo} foot={`${items.done} completed`} />
            <StatCard label="Overdue" value={items.overdue} tone="danger" icon={AlarmClock} foot={items.overdue ? "Needs follow-up" : "Nothing late"} />
            <StatCard label="Due this week" value={items.due_soon} tone="success" icon={CalendarCheck2} foot="Next 7 days" />
          </div>

          <div className="grid-2">
            <section className="card" aria-labelledby="attention-heading">
              <div className="card-head">
                <div>
                  <h2 className="card-title" id="attention-heading"><AlarmClock size={16} /> Needs attention</h2>
                  <div className="card-sub">Overdue or due within 7 days</div>
                </div>
                <Link to="/action-items" className="btn btn-ghost btn-sm">View all <ArrowRight size={14} /></Link>
              </div>
              {attention.length === 0 ? (
                <EmptyState icon={CheckCircle2} title="Nothing urgent">No action items are overdue or due this week.</EmptyState>
              ) : (
                <ul className="list">
                  {attention.map((item) => <ActionItemRow key={item.id} item={item} showMeeting />)}
                </ul>
              )}
            </section>

            <section className="card" aria-labelledby="recent-heading">
              <div className="card-head">
                <h2 className="card-title" id="recent-heading"><CalendarDays size={16} /> Recent meetings</h2>
                <Link to="/meetings" className="btn btn-ghost btn-sm">All <ArrowRight size={14} /></Link>
              </div>
              <div>
                {recent.map((meeting) => <MeetingRow key={meeting.id} meeting={meeting} />)}
              </div>
            </section>
          </div>
        </>
      )}
    </>
  );
}

function StatCard({ label, value, tone, icon: Icon, foot }: {
  label: string;
  value: number;
  tone: "primary" | "success" | "warning" | "danger";
  icon: LucideIcon;
  foot: string;
}) {
  return (
    <div className="card stat" role="group" aria-label={label}>
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        <span className={`stat-icon ${tone}`}><Icon size={18} aria-hidden /></span>
      </div>
      <div className="stat-value" style={tone === "danger" && value > 0 ? { color: "var(--danger-text)" } : undefined}>{value}</div>
      <div className="stat-foot">{foot}</div>
    </div>
  );
}

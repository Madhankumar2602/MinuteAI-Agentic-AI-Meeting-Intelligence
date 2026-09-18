import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Bot,
  CalendarDays,
  Check,
  CheckCircle2,
  ClipboardCopy,
  Mail,
  Pencil,
  Play,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "../api/endpoints";
import type { AgentRun, Proposal, ProposalStatus } from "../api/types";
import { useFeedback } from "../components/feedback-context";
import { Badge, EmptyState, ErrorBanner, PageHeader, PageLoading, Spinner } from "../components/ui";
import { PROPOSAL_KIND_LABEL, STEP_LABEL, describeStep, mailtoHref } from "../lib/agent";
import { SOURCE_KIND_LABEL, sourceHref } from "../lib/ask";
import { formatDate, formatDateTime } from "../lib/format";

const TABS: { status: ProposalStatus; label: string }[] = [
  { status: "proposed", label: "Needs approval" },
  { status: "approved", label: "Approved" },
  { status: "rejected", label: "Rejected" },
];

const PRIORITY_TONE = { high: "danger", medium: "warning", low: "neutral" } as const;

export function FollowUpsPage() {
  const queryClient = useQueryClient();
  const { toast } = useFeedback();
  const [tab, setTab] = useState<ProposalStatus>("proposed");

  const proposals = useQuery({ queryKey: ["proposals"], queryFn: () => api.listProposals() });
  const runs = useQuery({ queryKey: ["agent-runs"], queryFn: () => api.listAgentRuns(1) });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["proposals"] });
    void queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const run = useMutation({
    mutationFn: api.runAgent,
    onSuccess: (result) => {
      refresh();
      setTab("proposed");
      if (result.status === "failed") {
        toast({ tone: "error", title: "The agent run failed", text: `Reason: ${result.error_code}` });
      } else {
        toast({
          title: result.proposals_created ? `${result.proposals_created} new follow-up${result.proposals_created === 1 ? "" : "s"}` : "Nothing new to follow up",
          text: result.proposals_created ? "Review each draft below before sending it." : "Everything the agent found has already been proposed.",
        });
      }
    },
    onError: () => refresh(),
  });

  const lastRun = run.data ?? runs.data?.[0];
  const items = proposals.data?.items ?? [];
  const shown = items.filter((p) => p.status === tab);

  return (
    <>
      <PageHeader
        eyebrow={<><Bot size={13} /> Follow-up agent</>}
        title="Follow-ups"
        subtitle="The agent reviews your meetings for overdue work, open decisions, and topics that keep coming back, then drafts follow-ups. Nothing is sent until you approve it."
        actions={
          <button type="button" className="btn btn-gradient" onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? <><Spinner label="Agent running" /> Reviewing meetings…</> : <><Play size={15} /> Run agent</>}
          </button>
        }
      />

      {run.isError && <ErrorBanner error={run.error} title="The agent could not run." />}
      {lastRun && <RunTrace run={lastRun} />}

      <div className="chips" role="tablist" aria-label="Follow-up status" style={{ margin: "18px 0 14px" }}>
        {TABS.map(({ status, label }) => {
          const count = items.filter((p) => p.status === status).length;
          return (
            <button key={status} type="button" role="tab" className="chip" aria-selected={tab === status} aria-pressed={tab === status} onClick={() => setTab(status)}>
              {label} <span className="chip-count">{count}</span>
            </button>
          );
        })}
      </div>

      {proposals.isPending ? (
        <PageLoading />
      ) : proposals.isError ? (
        <ErrorBanner error={proposals.error} title="Could not load follow-ups." />
      ) : shown.length === 0 ? (
        <div className="card">
          <EmptyState icon={tab === "proposed" ? CheckCircle2 : Sparkles} title={tab === "proposed" ? "No follow-ups waiting" : `Nothing ${tab} yet`}>
            {tab === "proposed" ? "Run the agent to review your meetings for anything that needs a follow-up." : undefined}
          </EmptyState>
        </div>
      ) : (
        <div className="stack">
          {shown.map((p) => <ProposalCard key={p.id} proposal={p} onDecided={refresh} />)}
        </div>
      )}
    </>
  );
}

function RunTrace({ run }: { run: AgentRun }) {
  return (
    <details className="card agent-trace" open={run.status === "failed" || undefined}>
      <summary className="card-pad row-between">
        <span className="row" style={{ gap: 8 }}>
          <strong>Last run</strong>
          <span className="muted small">{formatDateTime(run.started_at)}</span>
          {run.used_fallback && <Badge tone="warning">Template drafts</Badge>}
          {run.status === "failed" && <Badge tone="danger">Failed</Badge>}
        </span>
        <span className="muted small">
          {run.candidates_found} found · {run.proposals_created} proposed
        </span>
      </summary>
      <ol className="agent-steps" aria-label="What the agent did">
        {run.steps.map((step, i) => (
          <li key={i}>
            <strong>{STEP_LABEL[String(step.step)] ?? String(step.step)}</strong>
            <span className="muted small">{describeStep(step)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function ProposalCard({ proposal: p, onDecided }: { proposal: Proposal; onDecided: () => void }) {
  const { toast } = useFeedback();
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(p.draft_subject);
  const [body, setBody] = useState(p.draft_body);

  const approve = useMutation({
    mutationFn: () =>
      api.approveProposal(
        p.id,
        editing ? { subject: subject.trim() || undefined, body: body.trim() || undefined } : {},
      ),
    onSuccess: () => {
      toast({ title: "Follow-up approved", text: "Send it from your email or chat when you are ready." });
      onDecided();
    },
  });
  const reject = useMutation({
    mutationFn: () => api.rejectProposal(p.id),
    onSuccess: () => {
      toast({ tone: "info", title: "Follow-up rejected", text: "It will not be proposed again." });
      onDecided();
    },
  });
  const busy = approve.isPending || reject.isPending;
  const finalSubject = p.final_subject ?? p.draft_subject;
  const finalBody = p.final_body ?? p.draft_body;

  async function copy() {
    await navigator.clipboard?.writeText(`${finalSubject}\n\n${finalBody}`);
    toast({ title: "Copied to clipboard" });
  }

  return (
    <article className="card proposal" aria-label={p.title}>
      <div className="card-pad stack-sm">
        <div className="row-between">
          <div className="row" style={{ gap: 6 }}>
            <Badge tone={PRIORITY_TONE[p.priority] ?? "neutral"} dot>{p.priority} priority</Badge>
            <Badge tone="violet">{PROPOSAL_KIND_LABEL[p.kind] ?? p.kind}</Badge>
            {p.drafted_by === "template" && <Badge tone="neutral">Template</Badge>}
          </div>
          <Link to={`/meetings/${p.meeting_id}`} className="faint xs row" style={{ gap: 5 }}>
            <CalendarDays size={12} aria-hidden /> {p.meeting_title} · {formatDate(p.meeting_date)}
          </Link>
        </div>
        <h3 className="proposal-title">{p.title}</h3>
        <p className="muted small" style={{ margin: 0 }}>{p.rationale}</p>
        <div className="row faint xs" style={{ gap: 6 }}>
          <Users size={13} aria-hidden /> To: {p.recipients.length ? p.recipients.join(", ") : "choose recipients when sending"}
        </div>

        {editing ? (
          <div className="stack-sm">
            <label className="sr-only" htmlFor={`subject-${p.id}`}>Subject</label>
            <input id={`subject-${p.id}`} className="input" value={subject} maxLength={300} onChange={(e) => setSubject(e.target.value)} />
            <label className="sr-only" htmlFor={`body-${p.id}`}>Message</label>
            <textarea id={`body-${p.id}`} className="textarea proposal-edit" rows={8} maxLength={5000} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
        ) : (
          <div className="proposal-draft">
            <div className="strong">{finalSubject}</div>
            <p className="passage">{finalBody}</p>
          </div>
        )}

        {p.sources.length > 0 && (
          <details className="proposal-sources">
            <summary className="faint xs">Based on {p.sources.length} passage{p.sources.length === 1 ? "" : "s"} from your meetings</summary>
            <ol>
              {p.sources.map((s) => (
                <li key={s.number}>
                  <span className="faint xs">{SOURCE_KIND_LABEL[s.kind]} · {s.meeting_title}</span>
                  <p className="passage small">{s.text}</p>
                  <Link to={sourceHref(s)} className="xs">Open <ArrowUpRight size={12} /></Link>
                </li>
              ))}
            </ol>
          </details>
        )}

        {(approve.isError || reject.isError) && <ErrorBanner error={approve.error ?? reject.error} title="Could not save your decision." />}

        {p.status === "proposed" ? (
          <div className="row">
            <button type="button" className="btn btn-gradient btn-sm" disabled={busy || (editing && (!subject.trim() || !body.trim()))} onClick={() => approve.mutate()}>
              <Check size={15} /> {editing ? "Approve with edits" : "Approve"}
            </button>
            {!editing && (
              <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setEditing(true)}>
                <Pencil size={14} /> Edit
              </button>
            )}
            <button type="button" className="btn btn-ghost btn-danger btn-sm" disabled={busy} onClick={() => reject.mutate()}>
              <X size={15} /> Reject
            </button>
          </div>
        ) : p.status === "approved" ? (
          <div className="row">
            <Badge tone="success" icon={CheckCircle2}>Approved {formatDate(p.decided_at)}</Badge>
            <a className="btn btn-sm" href={mailtoHref(p)}><Mail size={14} /> Open in email</a>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void copy()}><ClipboardCopy size={14} /> Copy</button>
          </div>
        ) : (
          <Badge tone="neutral">Rejected {formatDate(p.decided_at)}</Badge>
        )}
      </div>
    </article>
  );
}

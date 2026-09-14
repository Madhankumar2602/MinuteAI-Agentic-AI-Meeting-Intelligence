import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CalendarDays,
  CheckSquare,
  FileText,
  Gavel,
  LayoutList,
  Pencil,
  RotateCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  Users,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError } from "../api/client";
import { api } from "../api/endpoints";
import type { Decision, DecisionStatus, Intelligence, Media, Meeting, Transcript } from "../api/types";
import { ActionItemRow } from "../components/ActionItemRow";
import { useFeedback } from "../components/feedback-context";
import { MeetingInput } from "../components/MeetingInput";
import { SourceIcon } from "../components/MeetingRow";
import { ProcessingStatus } from "../components/ProcessingStatus";
import { Avatar, Badge, EmptyState, ErrorBanner, MeetingStatusBadge, PageLoading, Spinner } from "../components/ui";
import { formatBytes, formatDate, formatDateTime, formatDuration } from "../lib/format";
import { DECISION_STATUS_LABEL } from "../lib/labels";
import { inputProblem, type MeetingInputValue } from "../lib/meetingInput";
import { submitMeetingInput } from "../lib/submitInput";

const POLL_MS = 2000;
const isBusy = (m: Meeting | undefined) => m?.status === "queued" || m?.status === "processing";
const SOURCE_LABEL = { text: "Transcript", audio: "Audio recording", video: "Video recording" } as const;

type TabKey = "overview" | "actions" | "decisions" | "transcript";

/** 404 means "not there yet" for optional resources, not an error. */
async function orNull<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function MeetingDetailPage() {
  const { meetingId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast, confirm } = useFeedback();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = (searchParams.get("tab") as TabKey | null) ?? "overview";
  const setTab = (next: TabKey) => setSearchParams(next === "overview" ? {} : { tab: next }, { replace: true });

  const meeting = useQuery({
    queryKey: ["meeting", meetingId],
    queryFn: () => api.getMeeting(meetingId),
    refetchInterval: (q) => (isBusy(q.state.data) ? POLL_MS : false),
  });
  const busy = isBusy(meeting.data);

  const jobs = useQuery({
    queryKey: ["meeting", meetingId, "jobs"],
    queryFn: () => api.listMeetingJobs(meetingId),
    enabled: meeting.isSuccess,
    refetchInterval: busy ? POLL_MS : false,
  });
  const intelligence = useQuery({
    queryKey: ["meeting", meetingId, "intelligence"],
    queryFn: () => api.getIntelligence(meetingId),
    enabled: meeting.isSuccess,
  });
  const transcript = useQuery({
    queryKey: ["meeting", meetingId, "transcript"],
    queryFn: () => orNull(api.getTranscript(meetingId)),
    enabled: meeting.isSuccess,
  });
  const media = useQuery({
    queryKey: ["meeting", meetingId, "media"],
    queryFn: () => orNull(api.getMedia(meetingId)),
    enabled: meeting.isSuccess,
    staleTime: 10 * 60_000, // playback URLs are valid for 15 minutes
  });

  // When background processing finishes, reload everything it may have changed.
  const previousStatus = useRef(meeting.data?.status);
  useEffect(() => {
    const status = meeting.data?.status;
    const was = previousStatus.current;
    if (was && was !== status && !isBusy(meeting.data)) {
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
      void queryClient.invalidateQueries({ queryKey: ["action-items"] });
      if (status === "completed" && (was === "queued" || was === "processing")) {
        toast({ title: "Analysis complete", text: "Summary, decisions, and action items are ready." });
      }
      if (status === "failed") toast({ tone: "error", title: "Processing failed", text: "See the details below." });
    }
    previousStatus.current = status;
  }, [meeting.data, meetingId, queryClient, toast]);

  const processMutation = useMutation({
    mutationFn: (force: boolean) => api.process(meetingId, force),
    onSuccess: (result) => {
      toast(
        result.cached
          ? { tone: "info", title: "Already up to date", text: "Results already reflect this transcript — nothing to re-run." }
          : { title: "Analysis started", text: "This usually takes under a minute." },
      );
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteMeeting(meetingId),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["meeting", meetingId] });
      await queryClient.invalidateQueries({ queryKey: ["meetings"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      toast({ title: "Meeting deleted" });
      navigate("/meetings", { replace: true });
    },
  });

  if (meeting.isPending) return <PageLoading />;
  if (meeting.isError) {
    if (meeting.error instanceof ApiError && meeting.error.status === 404) {
      return (
        <div className="card">
          <EmptyState icon={TriangleAlert} title="Meeting not found" action={<Link className="btn" to="/meetings">Back to meetings</Link>}>
            It may have been deleted, or it belongs to another account.
          </EmptyState>
        </div>
      );
    }
    return <ErrorBanner error={meeting.error} title="Could not load this meeting." />;
  }

  const m = meeting.data;
  const latestJob = jobs.data?.[0];
  const hasInput = Boolean(transcript.data || media.data);
  const results = intelligence.data;
  const hasResults = Boolean(results?.summary);

  async function rerun() {
    if (hasResults) {
      const ok = await confirm({
        title: "Re-run the AI analysis?",
        message: "Summary, decisions, and action items are regenerated. Status changes you made to action items will be reset.",
        confirmLabel: "Re-run analysis",
        tone: "primary",
      });
      if (!ok) return;
    }
    processMutation.mutate(hasResults);
  }

  async function remove() {
    const ok = await confirm({
      title: `Delete “${m.title}”?`,
      message: "The meeting, its transcript, recording, and everything extracted from it are permanently deleted.",
      confirmLabel: "Delete meeting",
    });
    if (ok) deleteMutation.mutate();
  }

  return (
    <>
      <Link to="/meetings" className="back-link"><ArrowLeft size={16} /> Meetings</Link>

      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <div className="page-eyebrow">
            <CalendarDays size={13} /> {formatDateTime(m.meeting_date)}
            <span className="meta-sep" />
            <SourceIcon source={m.source_type} size={13} /> {SOURCE_LABEL[m.source_type]}
          </div>
          <div className="row" style={{ gap: 12 }}>
            <h1 className="page-title">{m.title}</h1>
            <MeetingStatusBadge status={m.status} />
          </div>
          {m.description && <div className="page-subtitle">{m.description}</div>}
        </div>
        <div className="row">
          {hasInput && !busy && (
            <button type="button" className={`btn ${hasResults ? "" : "btn-gradient"}`} disabled={processMutation.isPending} onClick={() => void rerun()}>
              {processMutation.isPending ? <Spinner label="Submitting" /> : hasResults ? <><RotateCw size={15} /> Re-run AI</> : <><Sparkles size={15} /> Process meeting</>}
            </button>
          )}
          <button
            type="button"
            className="btn btn-danger"
            disabled={deleteMutation.isPending || busy}
            title={busy ? "Wait for processing to finish" : undefined}
            onClick={() => void remove()}
          >
            <Trash2 size={15} /> Delete
          </button>
        </div>
      </div>

      <div className="stack">
        <ErrorBanner error={processMutation.error ?? deleteMutation.error} />

        {!hasInput && !busy && transcript.isSuccess && media.isSuccess && <AddInputCard meetingId={meetingId} />}

        {latestJob && (busy || latestJob.status === "FAILED" || !hasResults) && (
          <section className="card" aria-labelledby="processing-heading">
            <div className="card-head">
              <h2 className="card-title" id="processing-heading"><Sparkles size={16} /> Processing</h2>
            </div>
            <div className="card-pad"><ProcessingStatus job={latestJob} /></div>
          </section>
        )}

        {intelligence.isError && <ErrorBanner error={intelligence.error} title="Could not load results." />}

        {hasResults && results && (
          <>
            <div className="tabs" role="tablist" aria-label="Meeting sections">
              <TabButton id="overview" current={tab} onSelect={setTab} icon={LayoutList} label="Overview" />
              <TabButton id="actions" current={tab} onSelect={setTab} icon={CheckSquare} label="Action items" count={results.action_items.length} />
              <TabButton id="decisions" current={tab} onSelect={setTab} icon={Gavel} label="Decisions" count={results.decisions.length} />
              <TabButton id="transcript" current={tab} onSelect={setTab} icon={FileText} label="Transcript" />
            </div>

            <div role="tabpanel" aria-label={tab}>
              {tab === "overview" && (
                <OverviewTab meeting={m} results={results} transcript={transcript.data ?? null} media={media.data ?? null} onOpen={setTab} />
              )}
              {tab === "actions" && (
                <section className="card" aria-label="Action items">
                  {results.action_items.length === 0 ? (
                    <EmptyState icon={CheckSquare} title="No action items were identified" />
                  ) : (
                    <ul className="list">
                      {results.action_items.map((item) => <ActionItemRow key={item.id} item={item} showEvidence />)}
                    </ul>
                  )}
                </section>
              )}
              {tab === "decisions" && <DecisionsCard meetingId={meetingId} decisions={results.decisions} />}
              {tab === "transcript" && transcript.data && (
                <div className="stack">
                  {media.data && <RecordingCard media={media.data} transcript={transcript.data} />}
                  <TranscriptCard meetingId={meetingId} busy={busy} transcript={transcript.data} />
                </div>
              )}
            </div>
          </>
        )}

        {!hasResults && transcript.data && !busy && <TranscriptCard meetingId={meetingId} busy={busy} transcript={transcript.data} />}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------

function TabButton({ id, current, onSelect, icon: Icon, label, count }: {
  id: TabKey;
  current: TabKey;
  onSelect: (tab: TabKey) => void;
  icon: typeof LayoutList;
  label: string;
  count?: number;
}) {
  return (
    <button type="button" role="tab" className="tab" aria-selected={current === id} onClick={() => onSelect(id)}>
      <Icon size={15} aria-hidden /> {label}
      {count !== undefined && <span className="tab-count">{count}</span>}
    </button>
  );
}

function OverviewTab({ meeting, results, transcript, media, onOpen }: {
  meeting: Meeting;
  results: Intelligence;
  transcript: Transcript | null;
  media: Media | null;
  onOpen: (tab: TabKey) => void;
}) {
  const summary = results.summary!;
  const openItems = results.action_items.filter((i) => i.status === "pending" || i.status === "in_progress");
  const tasksByOwner = new Map<string, number>();
  for (const item of results.action_items) {
    if (item.owner_name) tasksByOwner.set(item.owner_name.toLowerCase(), (tasksByOwner.get(item.owner_name.toLowerCase()) ?? 0) + 1);
  }
  const verified =
    results.decisions.filter((d) => d.evidence_verified).length + results.action_items.filter((a) => a.evidence_verified).length;
  const extracted = results.decisions.length + results.action_items.length;

  return (
    <div className="grid-main">
      <div className="stack">
        <section className="card" aria-labelledby="summary-heading">
          <div className="card-head">
            <h2 className="card-title" id="summary-heading"><Sparkles size={16} /> Summary</h2>
            <span className="card-sub" title={`Prompt ${summary.prompt_version}`}>Generated {formatDateTime(summary.created_at)}</span>
          </div>
          <div className="card-pad">
            {summary.is_stale && (
              <div className="alert alert-warning" style={{ marginBottom: 14 }}>
                <TriangleAlert size={17} aria-hidden />
                <div>The transcript changed after this summary was generated. Re-run AI to update it.</div>
              </div>
            )}
            <p className="summary-text">{summary.summary_text}</p>
            {summary.key_points.length > 0 && (
              <ul className="key-points">
                {summary.key_points.map((point) => (
                  <li key={point}><Sparkles size={13} aria-hidden /> {point}</li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section className="card" aria-labelledby="next-heading">
          <div className="card-head">
            <h2 className="card-title" id="next-heading"><CheckSquare size={16} /> Next steps</h2>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpen("actions")}>All {results.action_items.length} action items</button>
          </div>
          {openItems.length === 0 ? (
            <EmptyState icon={CheckSquare} title={results.action_items.length ? "Every action item is done" : "No action items"} />
          ) : (
            <ul className="list">
              {openItems.slice(0, 4).map((item) => <ActionItemRow key={item.id} item={item} />)}
            </ul>
          )}
        </section>

        {results.decisions.length > 0 && (
          <section className="card" aria-labelledby="key-decisions-heading">
            <div className="card-head">
              <h2 className="card-title" id="key-decisions-heading"><Gavel size={16} /> Decisions</h2>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpen("decisions")}>Details</button>
            </div>
            <ul className="list">
              {results.decisions.map((d) => (
                <li key={d.id} className="list-item">
                  <Gavel size={16} style={{ color: "var(--primary)", marginTop: 3 }} aria-hidden />
                  <div className="body"><div className="title">{d.decision_text}</div></div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <div className="stack">
        <section className="card card-pad" aria-labelledby="glance-heading">
          <h2 className="card-title" id="glance-heading" style={{ marginBottom: 14 }}>At a glance</h2>
          <dl className="kv">
            <dt>Date</dt><dd>{formatDate(meeting.meeting_date)}</dd>
            <dt>Input</dt><dd>{SOURCE_LABEL[meeting.source_type]}</dd>
            {transcript?.duration_seconds != null && (<><dt>Duration</dt><dd>{formatDuration(transcript.duration_seconds)}</dd></>)}
            {transcript && (<><dt>Transcript</dt><dd>{transcript.word_count.toLocaleString()} words</dd></>)}
            <dt>Evidence</dt><dd>{verified} of {extracted} verified</dd>
            <dt>Model</dt><dd>{summary.model}</dd>
          </dl>
          {results.warnings.length > 0 && <p className="faint xs" style={{ marginTop: 12 }}>Warnings: {results.warnings.join("; ")}</p>}
        </section>

        {results.participants.length > 0 && (
          <section className="card card-pad" aria-labelledby="participants-heading">
            <h2 className="card-title" id="participants-heading" style={{ marginBottom: 14 }}><Users size={16} /> Participants</h2>
            <div className="people">
              {results.participants.map((p) => {
                const tasks = tasksByOwner.get(p.display_name.toLowerCase()) ?? 0;
                return (
                  <div key={p.id} className="person">
                    <Avatar name={p.display_name} />
                    <span className="strong" style={{ flex: 1 }}>{p.display_name}</span>
                    {tasks > 0 && <span className="faint small">{tasks} task{tasks === 1 ? "" : "s"}</span>}
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {media && transcript && <RecordingCard media={media} transcript={transcript} />}
      </div>
    </div>
  );
}

function RecordingCard({ media, transcript }: { media: Media; transcript: Transcript }) {
  return (
    <section className="card card-pad" aria-labelledby="recording-heading">
      <h2 className="card-title" id="recording-heading" style={{ marginBottom: 12 }}>Recording</h2>
      {media.download_url && media.content_type.startsWith("video/") ? (
        <video controls preload="metadata" src={media.download_url} style={{ width: "100%", borderRadius: 8 }} />
      ) : (
        media.download_url && <audio controls preload="metadata" src={media.download_url} style={{ width: "100%" }} />
      )}
      <p className="faint small" style={{ marginTop: 8 }}>
        {media.original_filename ?? "Recording"} · {formatBytes(media.size_bytes)}
        {transcript.duration_seconds != null && ` · ${formatDuration(transcript.duration_seconds)}`}
      </p>
    </section>
  );
}

function AddInputCard({ meetingId }: { meetingId: string }) {
  const queryClient = useQueryClient();
  const { toast, confirm } = useFeedback();
  const [value, setValue] = useState<MeetingInputValue>({ mode: "transcript", transcript: "", file: null });
  const [progress, setProgress] = useState<number | null>(null);
  const mutation = useMutation({
    mutationFn: () => {
      if (value.mode === "recording") setProgress(0);
      return submitMeetingInput(meetingId, value, {
        onProgress: setProgress,
        confirmReplaceTranscript: () =>
          confirm({ title: "Replace the typed transcript?", message: "The recording will be transcribed and replace the text transcript.", confirmLabel: "Replace", tone: "primary" }),
      });
    },
    onSuccess: () => toast({ title: "Analysis started", text: "This usually takes under a minute." }),
    onSettled: () => {
      setProgress(null);
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
    },
  });
  const problem = inputProblem(value);

  return (
    <section className="card" aria-labelledby="add-input-heading">
      <div className="card-head">
        <div>
          <h2 className="card-title" id="add-input-heading"><FileText size={16} /> Add meeting content</h2>
          <div className="card-sub">A transcript or recording is needed before analysis can run.</div>
        </div>
      </div>
      <div className="card-pad stack">
        <ErrorBanner error={mutation.error} />
        <MeetingInput value={value} onChange={setValue} disabled={mutation.isPending} uploadProgress={progress} />
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn btn-gradient" disabled={Boolean(problem) || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <Spinner label="Uploading" /> : <><Sparkles size={15} /> Save and analyse</>}
          </button>
        </div>
      </div>
    </section>
  );
}

function DecisionsCard({ meetingId, decisions }: { meetingId: string; decisions: Decision[] }) {
  const queryClient = useQueryClient();
  const { toast } = useFeedback();
  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: DecisionStatus }) => api.updateDecision(id, status),
    onSuccess: (_d, vars) => {
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId, "intelligence"] });
      toast({ title: `Decision marked ${DECISION_STATUS_LABEL[vars.status].toLowerCase()}` });
    },
  });

  return (
    <section className="card" aria-label="Decisions">
      <ErrorBanner error={update.error} />
      {decisions.length === 0 ? (
        <EmptyState icon={Gavel} title="No decisions were identified" />
      ) : (
        <ul className="list">
          {decisions.map((d) => (
            <li key={d.id} className="list-item">
              <span className="stat-icon primary" style={{ width: 32, height: 32 }}><Gavel size={15} aria-hidden /></span>
              <div className="body">
                <div className="title">{d.decision_text}</div>
                {d.context && <div className="muted small" style={{ marginTop: 2 }}>{d.context}</div>}
                <div style={{ marginTop: 8 }}>
                  {d.evidence_quote ? (
                    <>
                      {d.evidence_verified ? (
                        <Badge tone="success" icon={ShieldCheck}>Evidence verified</Badge>
                      ) : (
                        <span title="The quoted passage was not found in the transcript. Check this decision.">
                          <Badge tone="warning" icon={ShieldAlert}>Evidence not found</Badge>
                        </span>
                      )}
                      <div className="quote">“{d.evidence_quote}”</div>
                    </>
                  ) : (
                    <Badge tone="warning" icon={ShieldAlert}>No evidence quoted</Badge>
                  )}
                </div>
              </div>
              <label className="sr-only" htmlFor={`decision-${d.id}`}>Status of decision</label>
              <select
                id={`decision-${d.id}`}
                className="select"
                value={d.status}
                disabled={update.isPending}
                onChange={(e) => update.mutate({ id: d.id, status: e.target.value as DecisionStatus })}
              >
                {Object.entries(DECISION_STATUS_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Speaker names are highlighted; everything else is rendered as plain text. */
function FormattedTranscript({ content }: { content: string }) {
  return (
    <div className="transcript" tabIndex={0} aria-label="Transcript text">
      {content.split("\n").map((line, i) => {
        const match = /^([^:\n]{1,40}):(.*)$/.exec(line);
        return (
          <div key={i}>
            {match ? (<><span className="speaker">{match[1]}:</span>{match[2]}</>) : line || " "}
          </div>
        );
      })}
    </div>
  );
}

function TranscriptCard({ meetingId, busy, transcript }: { meetingId: string; busy: boolean; transcript: Transcript }) {
  const queryClient = useQueryClient();
  const { toast } = useFeedback();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(transcript.content);
  const save = useMutation({
    mutationFn: () => api.putTranscript(meetingId, draft.trim()),
    onSuccess: () => {
      setEditing(false);
      toast({ title: "Transcript saved", text: "Re-run AI to update the results." });
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
    },
  });

  return (
    <section className="card" aria-labelledby="transcript-heading">
      <div className="card-head">
        <div>
          <h2 className="card-title" id="transcript-heading"><FileText size={16} /> Transcript</h2>
          <div className="card-sub">
            {transcript.source === "transcription" ? `Transcribed by ${transcript.transcription_model}` : "Typed or pasted"} · {transcript.word_count.toLocaleString()} words · updated {formatDate(transcript.updated_at)}
          </div>
        </div>
        {!editing && (
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setEditing(true)}>
            <Pencil size={14} /> Edit
          </button>
        )}
      </div>
      <div className="card-pad stack">
        <ErrorBanner error={save.error} />
        {editing ? (
          <>
            <label className="sr-only" htmlFor="transcript-editor">Transcript</label>
            <textarea id="transcript-editor" className="textarea" style={{ minHeight: 360 }} value={draft} onChange={(e) => setDraft(e.target.value)} />
            <div className="row-between">
              <span className="hint">Saving marks the current results as out of date.</span>
              <div className="row">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setEditing(false); setDraft(transcript.content); }}>Cancel</button>
                <button type="button" className="btn btn-primary btn-sm" disabled={save.isPending || draft.trim().length < 20} onClick={() => save.mutate()}>
                  Save transcript
                </button>
              </div>
            </div>
          </>
        ) : (
          <FormattedTranscript content={transcript.content} />
        )}
      </div>
    </section>
  );
}

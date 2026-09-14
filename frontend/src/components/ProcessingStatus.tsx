/**
 * Where a meeting's processing run is, as a four-step progress indicator plus
 * the detailed event timeline from the job record.
 */
import { AlertTriangle, Check, FileAudio, ListChecks, Loader2, Sparkles, X } from "lucide-react";

import type { Job, JobEvent } from "../api/types";
import { formatDateTime } from "../lib/format";
import { JobStatusBadge } from "./ui";

type StepState = "done" | "active" | "failed" | "todo";

interface Step {
  key: string;
  label: string;
  sub?: string;
  icon: typeof Check;
  state: StepState;
}

function stepsFor(job: Job): Step[] {
  const types = new Set(job.events.map((e) => e.type));
  const recording = types.has("transcription_started") || job.result?.transcribed === true;
  const failed = job.status === "FAILED";
  const done = job.status === "COMPLETED";
  const transcribed = types.has("transcription_completed");
  // Any progress event implies the run started, even if the log is incomplete.
  const started = job.status === "PROCESSING" || types.has("started") || types.has("transcription_started");

  const steps: Step[] = [{ key: "queued", label: "Queued", icon: ListChecks, state: "done" }];

  if (recording) {
    const state: StepState = transcribed || done ? "done" : failed ? "failed" : started ? "active" : "todo";
    steps.push({ key: "transcribe", label: "Transcribing", sub: "Speech to text", icon: FileAudio, state });
  }

  const analysisReached = recording ? transcribed : started;
  steps.push({
    key: "analyse",
    label: "Analysing",
    sub: "Decisions and actions",
    icon: Sparkles,
    state: done ? "done" : failed ? (analysisReached || !recording ? "failed" : "todo") : analysisReached ? "active" : "todo",
  });

  steps.push({ key: "done", label: failed ? "Failed" : "Ready", icon: failed ? X : Check, state: done ? "done" : failed ? "failed" : "todo" });

  // A retry is waiting: show the step as active rather than failed.
  if (job.status === "QUEUED" && job.attempts > 0) {
    for (const s of steps) if (s.state === "failed") s.state = "active";
  }
  return steps;
}

const EVENT_TEXT: Record<string, string> = {
  queued: "Queued",
  started: "Worker started",
  transcription_started: "Transcribing the recording",
  transcription_completed: "Transcription finished",
  retry_scheduled: "Attempt failed — retry scheduled",
  lease_expired_requeued: "Worker stopped responding — requeued",
  completed: "Completed",
  failed: "Failed",
};

function eventTone(type: string): string {
  if (type === "completed" || type === "transcription_completed") return "success";
  if (type === "failed") return "danger";
  if (type === "retry_scheduled" || type === "lease_expired_requeued") return "warning";
  return "";
}

function eventDetail(event: JobEvent): string | null {
  const d = event.detail ?? {};
  const parts: string[] = [];
  if (typeof d.attempt === "number") parts.push(`attempt ${d.attempt}`);
  if (typeof d.words === "number") parts.push(`${d.words} words`);
  if (typeof d.error_code === "string") parts.push(d.error_code.replaceAll("_", " "));
  if (d.cached === true) parts.push("served from cache");
  return parts.length ? parts.join(" · ") : null;
}

export function ProcessingStatus({ job, showTimeline = true }: { job: Job; showTimeline?: boolean }) {
  const steps = stepsFor(job);
  const retrying = job.status === "QUEUED" && job.attempts > 0;

  return (
    <div className="stack">
      <div className="row-between">
        <div className="row">
          <JobStatusBadge status={job.status} />
          <span className="faint small">Attempt {Math.max(job.attempts, 1)} of {job.max_attempts}</span>
        </div>
        {job.finished_at && <span className="faint small">Finished {formatDateTime(job.finished_at)}</span>}
      </div>

      <ol className="stepper" style={{ ["--steps" as string]: steps.length, listStyle: "none", padding: 0, margin: "4px 0" }} aria-label="Processing steps">
        {steps.map(({ key, label, sub, icon: Icon, state }) => (
          <li key={key} className={`step ${state === "todo" ? "" : state}`} aria-current={state === "active" ? "step" : undefined}>
            <span className="step-dot">
              {state === "active" ? <Loader2 size={15} className="spin-icon" style={{ animation: "spin 1s linear infinite" }} /> : state === "done" ? <Check size={15} strokeWidth={3} /> : <Icon size={15} />}
            </span>
            <span className="step-label">{label}</span>
            {sub && <span className="step-sub">{sub}</span>}
          </li>
        ))}
      </ol>

      {job.status === "FAILED" && job.error && (
        <div className="alert alert-danger" role="alert">
          <AlertTriangle size={17} aria-hidden />
          <div>{job.error.message}</div>
        </div>
      )}
      {retrying && job.error && (
        <div className="alert alert-warning">
          <AlertTriangle size={17} aria-hidden />
          <div>
            {job.error.message} Retrying automatically{job.next_attempt_at ? ` at ${formatDateTime(job.next_attempt_at)}` : ""}.
          </div>
        </div>
      )}

      {showTimeline && (
        <details>
          <summary className="faint small" style={{ cursor: "pointer" }}>Show processing log ({job.events.length} events)</summary>
          <ol className="timeline" aria-label="Processing timeline" style={{ marginTop: 12 }}>
            {job.events.map((event, index) => {
              const detail = eventDetail(event);
              return (
                <li key={`${event.at}-${index}`} className={eventTone(event.type)}>
                  <span className="tl-dot" aria-hidden />
                  <div>
                    <div>{EVENT_TEXT[event.type] ?? event.type}</div>
                    <div className="faint xs">
                      {formatDateTime(event.at)}
                      {detail && ` · ${detail}`}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        </details>
      )}

      {job.result && job.status === "COMPLETED" && (
        <p className="faint small">
          Found {job.result.decisions} decisions, {job.result.action_items} action items, and {job.result.participants} participants
          {job.result.transcribed && " from the recording"}.
        </p>
      )}
    </div>
  );
}

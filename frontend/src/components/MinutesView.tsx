import {
  CheckSquare,
  CircleHelp,
  Gavel,
  ListChecks,
  MessagesSquare,
  Route,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Tags,
  TriangleAlert,
} from "lucide-react";

import type { ActionItem, Minutes } from "../api/types";
import { formatDateTime, formatDuration } from "../lib/format";
import { DECISION_STATUS_LABEL } from "../lib/labels";
import { useMinutes } from "../lib/minutes";
import { ActionItemRow } from "./ActionItemRow";
import { MinutesPdfCard } from "./MinutesPdf";
import { Avatar, Badge, EmptyState, ErrorBanner, Skeleton } from "./ui";

const INPUT_LABEL = { transcript: "Transcript", notes: "Meeting notes", recording: "Recording" } as const;

export function MinutesTab({ meetingId, actionItems }: { meetingId: string; actionItems: ActionItem[] }) {
  const minutes = useMinutes(meetingId, true);
  if (minutes.isPending) {
    return (
      <div className="stack" aria-busy="true" aria-label="Loading minutes">
        <Skeleton height={140} />
        <Skeleton height={220} />
      </div>
    );
  }
  if (minutes.isError) return <ErrorBanner error={minutes.error} title="Could not load the minutes." />;
  const m = minutes.data;

  return (
    <div className="grid-main">
      <div className="stack">
        <section className="card" aria-labelledby="summary-heading">
          <div className="card-head">
            <h2 className="card-title" id="summary-heading"><Sparkles size={16} /> Executive summary</h2>
            <span className="card-sub">Extracted {formatDateTime(m.source.extracted_at)}</span>
          </div>
          <div className="card-pad">
            {m.is_stale && (
              <div className="alert alert-warning" style={{ marginBottom: 14 }}>
                <TriangleAlert size={17} aria-hidden />
                <div>The transcript changed after these minutes were produced. Re-run AI to update them.</div>
              </div>
            )}
            <p className="summary-text">{m.executive_summary}</p>
            {m.keywords.length > 0 && (
              <div className="keyword-row" aria-label="Keywords">
                <Tags size={14} aria-hidden />
                {m.keywords.map((k) => <span key={k} className="keyword">{k}</span>)}
              </div>
            )}
          </div>
        </section>

        {m.key_points.length > 0 && (
          <section className="card" aria-labelledby="points-heading">
            <div className="card-head"><h2 className="card-title" id="points-heading"><MessagesSquare size={16} /> Key discussion points</h2></div>
            <ul className="key-points card-pad" style={{ margin: 0 }}>
              {m.key_points.map((point) => <li key={point}><Sparkles size={13} aria-hidden /> {point}</li>)}
            </ul>
          </section>
        )}

        <section className="card" aria-labelledby="mom-decisions-heading">
          <div className="card-head">
            <h2 className="card-title" id="mom-decisions-heading"><Gavel size={16} /> Decisions</h2>
            <span className="card-sub">{m.decisions.length}</span>
          </div>
          {m.decisions.length === 0 ? (
            <EmptyState icon={Gavel} title="No decisions were recorded" />
          ) : (
            <ol className="list numbered">
              {m.decisions.map((d) => (
                <li key={d.number} className="list-item">
                  <span className="num" aria-hidden>{d.number}</span>
                  <div className="body">
                    <div className="title">{d.text}</div>
                    {d.context && <div className="muted small" style={{ marginTop: 2 }}>{d.context}</div>}
                  </div>
                  <Badge tone={d.status === "open" ? "primary" : "neutral"}>{DECISION_STATUS_LABEL[d.status]}</Badge>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="card" aria-labelledby="mom-actions-heading">
          <div className="card-head">
            <h2 className="card-title" id="mom-actions-heading"><CheckSquare size={16} /> Action items</h2>
            <span className="card-sub">Owner · deadline · status</span>
          </div>
          {actionItems.length === 0 ? (
            <EmptyState icon={CheckSquare} title="No action items were recorded" />
          ) : (
            <ul className="list">{actionItems.map((item) => <ActionItemRow key={item.id} item={item} />)}</ul>
          )}
        </section>

        <div className="grid-2">
          <section className="card" aria-labelledby="pending-heading">
            <div className="card-head"><h2 className="card-title" id="pending-heading"><CircleHelp size={16} /> Pending &amp; unresolved</h2></div>
            {m.pending_items.length === 0 ? (
              <p className="card-pad muted small" style={{ margin: 0 }}>Nothing was left open.</p>
            ) : (
              <ul className="plain-list card-pad">
                {m.pending_items.map((p) => (
                  <li key={p.item}>
                    <span>{p.item}</span>
                    {!p.evidence_verified && <span title="No supporting quote was found in the input"><Badge tone="warning" icon={ShieldAlert}>Check</Badge></span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card" aria-labelledby="next-steps-heading">
            <div className="card-head"><h2 className="card-title" id="next-steps-heading"><Route size={16} /> Next steps</h2></div>
            {m.next_steps.length === 0 ? (
              <p className="card-pad muted small" style={{ margin: 0 }}>No next steps were recorded.</p>
            ) : (
              <div className="card-pad">
                <ol className="steps-list">{m.next_steps.map((s) => <li key={s}>{s}</li>)}</ol>
                {m.next_steps_derived && <p className="faint xs" style={{ marginTop: 8 }}>Derived from the open action items.</p>}
              </div>
            )}
          </section>
        </div>
      </div>

      <div className="stack">
        <MinutesPdfCard meetingId={meetingId} summary={pdfSummary(m)} />

        {m.review_flags.length > 0 && (
          <section className="card card-pad review-card" aria-labelledby="review-heading">
            <h2 className="card-title" id="review-heading" style={{ marginBottom: 10 }}><ShieldAlert size={16} /> Needs review</h2>
            <ul className="plain-list">
              {m.review_flags.map((f) => <li key={f.kind + f.message}>{f.message}</li>)}
            </ul>
          </section>
        )}

        {m.speakers.length > 0 && (
          <section className="card card-pad" aria-labelledby="speakers-heading">
            <h2 className="card-title" id="speakers-heading" style={{ marginBottom: 14 }}><MessagesSquare size={16} /> Speakers</h2>
            <ul className="speakers">
              {m.speakers.map((s) => (
                <li key={s.name}>
                  <div className="row" style={{ gap: 10, flexWrap: "nowrap" }}>
                    <Avatar name={s.name} />
                    <span className="strong" style={{ flex: 1 }}>{s.name}</span>
                    {s.words > 0 && <span className="faint small">{Math.round(s.share * 100)}% · {s.turns} turn{s.turns === 1 ? "" : "s"}</span>}
                  </div>
                  {s.words > 0 && (
                    <div className="progress thin" aria-hidden><span style={{ width: `${Math.round(s.share * 100)}%` }} /></div>
                  )}
                  {s.contribution && <p className="muted small" style={{ margin: "6px 0 0" }}>{s.contribution}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="card card-pad" aria-labelledby="source-heading">
          <h2 className="card-title" id="source-heading" style={{ marginBottom: 14 }}><ListChecks size={16} /> Source</h2>
          <dl className="kv">
            <dt>Input</dt><dd>{INPUT_LABEL[m.source.input_kind]}</dd>
            {m.source.recording_filename && (<><dt>File</dt><dd className="truncate" title={m.source.recording_filename}>{m.source.recording_filename}</dd></>)}
            {m.source.duration_seconds != null && (<><dt>Duration</dt><dd>{formatDuration(m.source.duration_seconds)}</dd></>)}
            <dt>Words</dt><dd>{m.source.transcript_words.toLocaleString()}</dd>
            {m.source.transcription_model && (<><dt>Transcription</dt><dd>{m.source.transcription_model}</dd></>)}
            <dt>Model</dt><dd>{m.source.extraction_model}</dd>
            <dt>Prompt</dt><dd>{m.source.prompt_version}</dd>
            <dt>Evidence</dt>
            <dd>
              {m.source.evidence_total === 0 ? "—" : (
                <Badge tone={m.source.evidence_verified === m.source.evidence_total ? "success" : "warning"} icon={m.source.evidence_verified === m.source.evidence_total ? ShieldCheck : ShieldAlert}>
                  {m.source.evidence_verified} of {m.source.evidence_total} verified
                </Badge>
              )}
            </dd>
          </dl>
        </section>
      </div>
    </div>
  );
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function pdfSummary(m: Minutes): string {
  return `${plural(m.decisions.length, "decision")} · ${plural(m.action_items.length, "action item")} · ${plural(m.participants.length, "participant")}.`;
}

import { useMutation } from "@tanstack/react-query";
import { ArrowUpRight, CalendarDays, MessageSquareText, SearchX, Send, Sparkles } from "lucide-react";
import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";

import { api } from "../api/endpoints";
import type { AskResponse, AskSource } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { TranscriptPassage } from "../components/TranscriptText";
import { Avatar, Badge, EmptyState, ErrorBanner, PageHeader, Spinner } from "../components/ui";
import { formatDate } from "../lib/format";
import { SOURCE_KIND_LABEL, sourceHref, splitCitations } from "../lib/ask";

const EXAMPLES = [
  "What did we decide about the database migration?",
  "Which action items are still open, and who owns them?",
  "What topics were left unresolved?",
  "What happens next after our last meeting?",
];

interface Entry {
  id: number;
  question: string;
  response: AskResponse | null;
  error: unknown;
}

export function AskPage() {
  const { user } = useAuth();
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);

  const ask = useMutation({ mutationFn: (q: string) => api.ask({ question: q }) });

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [entries]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const q = question.trim();
    if (q.length < 3 || ask.isPending) return;
    const id = nextId.current++;
    setEntries((all) => [...all, { id, question: q, response: null, error: null }]);
    setQuestion("");
    ask.mutate(q, {
      onSuccess: (response) => setEntries((all) => all.map((e) => (e.id === id ? { ...e, response } : e))),
      onError: (error) => setEntries((all) => all.map((e) => (e.id === id ? { ...e, error } : e))),
    });
  }

  return (
    <>
      <PageHeader
        eyebrow={<><Sparkles size={13} /> Retrieval-augmented answers</>}
        title="Ask your meetings"
        subtitle="Answers come only from your meetings' transcripts and minutes, with every statement linked to its source."
      />

      <div className="ask-thread" aria-live="polite">
        {entries.length === 0 ? (
          <div className="card">
            <EmptyState icon={MessageSquareText} title="Ask anything about your past meetings">
              MinuteAI searches your transcripts and minutes, then answers with citations. If the answer is not there, it says so.
            </EmptyState>
            <div className="chips" style={{ justifyContent: "center", padding: "0 20px 24px" }} aria-label="Example questions">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" className="chip" onClick={() => setQuestion(example)}>{example}</button>
              ))}
            </div>
          </div>
        ) : (
          entries.map((entry) => (
            <article key={entry.id} className="ask-entry" aria-label={`Question: ${entry.question}`}>
              <div className="ask-question">
                <Avatar name={user?.full_name ?? "You"} />
                <p>{entry.question}</p>
              </div>
              {entry.error ? (
                <ErrorBanner error={entry.error} title="The question could not be answered." />
              ) : entry.response ? (
                <AnswerCard entryId={entry.id} response={entry.response} />
              ) : (
                <div className="card ask-answer pending" aria-busy="true">
                  <Spinner label="Searching your meetings" /> <span className="muted small">Searching your meetings and checking sources…</span>
                </div>
              )}
            </article>
          ))
        )}
        <div ref={bottom} />
      </div>

      <form className="ask-composer card" onSubmit={submit}>
        <label className="sr-only" htmlFor="ask-question">Your question</label>
        <textarea
          id="ask-question"
          className="textarea"
          rows={2}
          maxLength={1000}
          placeholder="e.g. Who is responsible for the migration runbook, and when is it due?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
          }}
        />
        <div className="row-between">
          <span className="faint xs">Ctrl + Enter to send</span>
          <button type="submit" className="btn btn-gradient" disabled={question.trim().length < 3 || ask.isPending}>
            {ask.isPending ? <Spinner label="Asking" /> : <><Send size={15} /> Ask</>}
          </button>
        </div>
      </form>
    </>
  );
}

function AnswerCard({ entryId, response }: { entryId: number; response: AskResponse }) {
  const anchor = (n: number) => `src-${entryId}-${n}`;
  if (response.status !== "answered") {
    return (
      <div className="card ask-answer not-found">
        <div className="row" style={{ gap: 10 }}>
          <SearchX size={18} aria-hidden />
          <strong>{response.status === "no_indexed_meetings" ? "No meetings to search yet" : "Not found in your meetings"}</strong>
        </div>
        <p className="muted" style={{ margin: "6px 0 0" }}>{response.answer}</p>
        {response.status === "no_indexed_meetings" && (
          <Link className="btn btn-sm" to="/meetings/new" style={{ marginTop: 12 }}>Add a meeting</Link>
        )}
      </div>
    );
  }
  return (
    <div className="card ask-answer">
      <div className="card-pad">
        <p className="ask-text">{renderWithCitations(response.answer, anchor)}</p>
        <p className="faint xs" style={{ marginTop: 10 }}>
          Based on {response.sources.length} cited of {response.retrieved} retrieved passage{response.retrieved === 1 ? "" : "s"}
          {response.model ? ` · ${response.model}` : ""} · {(response.latency_ms / 1000).toFixed(1)} s
        </p>
      </div>
      <ol className="ask-sources" aria-label="Sources">
        {response.sources.map((source) => <SourceCard key={source.number} id={anchor(source.number)} source={source} />)}
      </ol>
    </div>
  );
}

function renderWithCitations(text: string, anchor: (n: number) => string): ReactNode {
  return splitCitations(text).map((part, i) =>
    typeof part === "number" ? (
      <a key={i} className="cite" href={`#${anchor(part)}`} aria-label={`Source ${part}`}>{part}</a>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

function SourceCard({ id, source }: { id: string; source: AskSource }) {
  return (
    <li id={id} className="ask-source">
      <div className="row-between" style={{ gap: 10 }}>
        <div className="row" style={{ gap: 10, minWidth: 0 }}>
          <span className="cite static" aria-hidden>{source.number}</span>
          <div style={{ minWidth: 0 }}>
            <Link to={sourceHref(source)} className="result-title truncate">{source.meeting_title}</Link>
            <div className="faint xs row" style={{ gap: 5 }}><CalendarDays size={12} aria-hidden /> {formatDate(source.meeting_date)}</div>
          </div>
        </div>
        <Badge tone={source.kind === "transcript" ? "neutral" : "violet"}>{SOURCE_KIND_LABEL[source.kind]}</Badge>
      </div>
      <div className="ask-source-text">
        {source.kind === "transcript" ? <TranscriptPassage content={source.text} /> : <p className="passage">{source.text}</p>}
      </div>
      <Link to={sourceHref(source)} className="btn btn-ghost btn-sm">
        {source.kind === "transcript" ? "Open in transcript" : "Open minutes"} <ArrowUpRight size={14} />
      </Link>
    </li>
  );
}

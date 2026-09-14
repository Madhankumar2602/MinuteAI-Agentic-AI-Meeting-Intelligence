import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CalendarDays, Search, SearchX, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api } from "../api/endpoints";
import type { SearchResult } from "../api/types";
import { TranscriptPassage } from "../components/TranscriptText";
import { Badge, EmptyState, ErrorBanner, PageHeader, Skeleton } from "../components/ui";
import { formatDate } from "../lib/format";
import { matchStrength } from "../lib/search";
import { useDebounced } from "../lib/useDebounced";

const MIN_QUERY = 2;
const EXAMPLES = [
  "What did we decide about the database?",
  "Who is responsible for the report?",
  "Were there any customer complaints?",
  "What are the risks or blockers?",
];

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const [text, setText] = useState(params.get("q") ?? "");
  const q = useDebounced(text.trim(), 400);

  // Keep the URL in step with the query so a search can be shared or revisited.
  const urlQuery = params.get("q") ?? "";
  useEffect(() => {
    if (urlQuery !== q) setParams(q ? { q } : {}, { replace: true });
  }, [q, urlQuery, setParams]);

  const search = useQuery({
    queryKey: ["search", q],
    queryFn: () => api.search({ q, limit: 20 }),
    enabled: q.length >= MIN_QUERY,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const results = q.length >= MIN_QUERY ? search.data?.results : undefined;

  return (
    <>
      <PageHeader
        eyebrow={<><Sparkles size={13} /> Semantic search</>}
        title="Search your meetings"
        subtitle="Finds passages by meaning, so “signed out” also finds “logged out”."
      />

      <form className="search search-hero" role="search" onSubmit={(e) => e.preventDefault()}>
        <Search size={20} />
        <label className="sr-only" htmlFor="semantic-search">Search meeting transcripts</label>
        <input
          id="semantic-search"
          className="input"
          type="search"
          autoFocus
          maxLength={500}
          placeholder="Ask about anything that was said…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </form>

      {q.length < MIN_QUERY ? (
        <div className="card">
          <EmptyState icon={Search} title="Search across every processed meeting">
            Type a question or a topic. Results are the transcript passages closest in meaning.
          </EmptyState>
          <div className="chips" style={{ justifyContent: "center", padding: "0 20px 24px" }} aria-label="Example searches">
            {EXAMPLES.map((example) => (
              <button key={example} type="button" className="chip" onClick={() => setText(example)}>{example}</button>
            ))}
          </div>
        </div>
      ) : search.isError ? (
        <ErrorBanner error={search.error} title="Search failed." />
      ) : !results ? (
        <div className="stack" aria-busy="true" aria-label="Searching">
          {[0, 1, 2].map((i) => <Skeleton key={i} height={120} />)}
        </div>
      ) : results.length === 0 ? (
        <div className="card">
          <EmptyState icon={SearchX} title="No matching passages">
            Only meetings that have been processed are searchable.
          </EmptyState>
        </div>
      ) : (
        <>
          <p className="muted small" aria-live="polite" style={{ margin: "0 2px 10px" }}>
            {results.length} passage{results.length === 1 ? "" : "s"}, closest match first
          </p>
          <ol className="stack results" aria-label="Search results" style={{ opacity: search.isPlaceholderData ? 0.6 : 1 }}>
            {results.map((result) => <ResultCard key={result.chunk_id} result={result} />)}
          </ol>
        </>
      )}
    </>
  );
}

function ResultCard({ result }: { result: SearchResult }) {
  const strength = matchStrength(result.score);
  const href = `/meetings/${result.meeting_id}?tab=transcript&from=${result.char_start}&to=${result.char_end}`;
  return (
    <li className="card result">
      <div className="card-head">
        <div className="result-meta">
          <Link to={href} className="result-title">{result.meeting_title}</Link>
          <span className="faint xs row" style={{ gap: 5 }}><CalendarDays size={12} aria-hidden /> {formatDate(result.meeting_date)}</span>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <span className="match-meter" title={`Similarity ${result.score.toFixed(2)}`}>
            <span style={{ width: `${Math.round(Math.max(0, Math.min(1, result.score)) * 100)}%` }} />
          </span>
          <Badge tone={strength.tone}>{strength.label}</Badge>
        </div>
      </div>
      <div className="card-pad">
        <TranscriptPassage content={result.content} />
        <Link to={href} className="btn btn-ghost btn-sm result-open">
          Open in transcript <ArrowUpRight size={14} />
        </Link>
      </div>
    </li>
  );
}

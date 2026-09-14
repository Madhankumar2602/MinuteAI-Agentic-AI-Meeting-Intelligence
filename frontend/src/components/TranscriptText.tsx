import { useEffect, useRef } from "react";

export type CharRange = { start: number; end: number };

const SPEAKER = /^([^:\n]{1,40}):(.*)$/;

/** Speaker names emphasised; one element per line. */
function Line({ text }: { text: string }) {
  const match = SPEAKER.exec(text);
  if (!match) return <>{text || " "}</>;
  return (
    <>
      <span className="speaker">{match[1]}:</span>
      {match[2]}
    </>
  );
}

/** A passage of transcript, e.g. a search result. */
export function TranscriptPassage({ content }: { content: string }) {
  return (
    <div className="passage">
      {content.split("\n").map((line, i) => (
        <div key={i}><Line text={line} /></div>
      ))}
    </div>
  );
}

/**
 * The full transcript. Lines overlapping `highlight` (character offsets into
 * `content`, as returned by search) are marked, and the first one is scrolled
 * into view, so a search result opens at the passage it came from.
 */
export function FormattedTranscript({ content, highlight }: { content: string; highlight?: CharRange | null }) {
  const firstMarked = useRef<HTMLDivElement>(null);
  useEffect(() => {
    firstMarked.current?.scrollIntoView?.({ block: "center" });
  }, [highlight?.start, highlight?.end]);

  // Character offsets of each line, and which lines the highlight touches.
  const lines: { text: string; marked: boolean }[] = [];
  let start = 0;
  for (const text of content.split("\n")) {
    const end = start + text.length;
    lines.push({ text, marked: Boolean(highlight && text.trim() && start < highlight.end && end > highlight.start) });
    start = end + 1; // the newline
  }
  const firstIndex = lines.findIndex((line) => line.marked);

  return (
    <div className="transcript" tabIndex={0} aria-label="Transcript text">
      {lines.map((line, i) => (
        <div
          key={i}
          ref={i === firstIndex ? firstMarked : undefined}
          className={line.marked ? "hl" : undefined}
          data-highlighted={line.marked || undefined}
        >
          <Line text={line.text} />
        </div>
      ))}
    </div>
  );
}

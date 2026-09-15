import type { AskSource } from "../api/types";

export const SOURCE_KIND_LABEL: Record<AskSource["kind"], string> = {
  transcript: "Transcript",
  summary: "Minutes · summary",
  decision: "Minutes · decision",
  action_item: "Minutes · action item",
  pending: "Minutes · pending",
  next_steps: "Minutes · next steps",
};

/** Split "A [1]. B [2][3]." into text and citation numbers, in order. */
export function splitCitations(text: string): (string | number)[] {
  const parts: (string | number)[] = [];
  let last = 0;
  for (const match of text.matchAll(/\[(\d+)\]/g)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    parts.push(Number(match[1]));
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

/** Where a source opens: the highlighted transcript passage, or the meeting's minutes. */
export function sourceHref(source: AskSource): string {
  if (source.kind === "transcript" && source.char_start != null && source.char_end != null) {
    return `/meetings/${source.meeting_id}?tab=transcript&from=${source.char_start}&to=${source.char_end}`;
  }
  return `/meetings/${source.meeting_id}`;
}

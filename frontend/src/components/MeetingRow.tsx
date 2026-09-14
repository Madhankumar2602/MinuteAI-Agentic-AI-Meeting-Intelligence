import { Mic, Type, Video } from "lucide-react";
import { Link } from "react-router";

import type { Meeting } from "../api/types";
import { MeetingStatusBadge } from "./ui";

export function SourceIcon({ source, size = 18 }: { source: Meeting["source_type"]; size?: number }) {
  const Icon = source === "audio" ? Mic : source === "video" ? Video : Type;
  return <Icon size={size} aria-hidden />;
}

const SOURCE_LABEL = { text: "Transcript", audio: "Audio", video: "Video" } as const;

export function MeetingRow({ meeting }: { meeting: Meeting }) {
  const date = new Date(meeting.meeting_date);
  return (
    <Link to={`/meetings/${meeting.id}`} className="meeting-row">
      <div className="meeting-date" aria-hidden>
        <div className="d">{date.getDate()}</div>
        <div className="m">{date.toLocaleString(undefined, { month: "short" })}</div>
      </div>
      <span className={`meeting-icon ${meeting.source_type}`} title={SOURCE_LABEL[meeting.source_type]}>
        <SourceIcon source={meeting.source_type} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="strong truncate">{meeting.title}</div>
        <div className="faint small truncate">
          {date.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}
          {" · "}
          {SOURCE_LABEL[meeting.source_type]}
          {meeting.description && ` · ${meeting.description}`}
        </div>
      </div>
      <MeetingStatusBadge status={meeting.status} />
    </Link>
  );
}

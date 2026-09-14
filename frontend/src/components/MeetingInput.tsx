import { Clock3, FileAudio, FileText, FileVideo, Mic, NotebookPen, UploadCloud, Video, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { formatBytes } from "../lib/format";
import {
  MAX_RECORDING_BYTES,
  MIN_TRANSCRIPT_CHARS,
  inputProblem,
  isFileMode,
  isTextMode,
  type InputMode,
  type MeetingInputValue,
} from "../lib/meetingInput";
import { ProgressBar } from "./ui";

interface Props {
  value: MeetingInputValue;
  onChange: (value: MeetingInputValue) => void;
  allowLater?: boolean;
  disabled?: boolean;
  uploadProgress?: number | null;
}

const MODES: { key: InputMode; label: string; desc: string; icon: typeof FileText }[] = [
  { key: "notes", label: "Meeting notes", desc: "Notes or a written description", icon: NotebookPen },
  { key: "transcript", label: "Transcript", desc: "From Zoom, Teams, or Meet", icon: FileText },
  { key: "audio", label: "Audio", desc: "MP3, WAV, M4A, OGG, FLAC", icon: Mic },
  { key: "video", label: "Video", desc: "MP4, MOV, WebM — audio is extracted", icon: Video },
  { key: "later", label: "Add later", desc: "Create the meeting now", icon: Clock3 },
];

const ACCEPT: Record<"audio" | "video", string> = {
  audio: ".mp3,.wav,.m4a,.aac,.ogg,.flac,.webm,audio/*",
  video: ".mp4,.mov,.webm,video/mp4,video/webm,video/quicktime",
};

const PLACEHOLDER = {
  transcript: "Priya: Let's start with the database migration.\nKarthik: Staging is fully migrated to PostgreSQL 16.\nPriya: Great — Karthik, can you prepare the runbook by Friday?",
  notes: "Budget review, 14 Sep\n- Q4 budget approved at 4.2M\n- Leela to circulate the revised forecast by Friday\n- Hiring freeze: undecided, revisit at the board meeting",
};

export function MeetingInput({ value, onChange, allowLater = false, disabled = false, uploadProgress = null }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const fileError = isFileMode(value.mode) && value.file ? inputProblem(value) : null;
  const chars = value.transcript.trim().length;
  const lines = value.transcript.split("\n").filter((l) => /^\s*[^:\n]{1,40}:\s*\S/.test(l)).length;

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file && !disabled) onChange({ ...value, file });
  }

  function selectMode(mode: InputMode) {
    // A file chosen for audio does not silently carry over to video, and vice versa.
    onChange({ ...value, mode, file: mode === value.mode ? value.file : null });
  }

  return (
    <div className="stack">
      <div className="mode-cards" role="group" aria-label="Meeting input">
        {MODES.filter((m) => allowLater || m.key !== "later").map(({ key, label, desc, icon: Icon }) => (
          <button key={key} type="button" className="mode-card" aria-pressed={value.mode === key} disabled={disabled} onClick={() => selectMode(key)}>
            <span className="mode-icon"><Icon size={17} aria-hidden /></span>
            <span className="mode-title">{label}</span>
            <span className="mode-desc">{desc}</span>
          </button>
        ))}
      </div>

      {isTextMode(value.mode) && (
        <div className="field">
          <div className="row-between">
            <label className="label" htmlFor="transcript">{value.mode === "notes" ? "Meeting notes" : "Transcript"}</label>
            <span className="faint xs" aria-live="polite">
              {chars > 0 && `${chars.toLocaleString()} characters${value.mode === "transcript" && lines ? ` · ${lines} speaker lines` : ""}`}
              {chars > 0 && chars < MIN_TRANSCRIPT_CHARS && ` · ${MIN_TRANSCRIPT_CHARS - chars} more needed`}
            </span>
          </div>
          <textarea
            id="transcript"
            className="textarea"
            value={value.transcript}
            disabled={disabled}
            placeholder={PLACEHOLDER[value.mode]}
            onChange={(e) => onChange({ ...value, transcript: e.target.value })}
          />
          <span className="hint">
            {value.mode === "notes" ? (
              <>Write what happened in any format. Name people next to their tasks so owners are captured.</>
            ) : (
              <>Tip: one speaker per line as <code>Name: what they said</code>. Named speakers make owners and speaker summaries accurate.</>
            )}
          </span>
        </div>
      )}

      {isFileMode(value.mode) && (
        <div className="stack-sm">
          {value.file ? (
            <div className="file-pill">
              <span className="meeting-icon audio">{value.mode === "video" ? <FileVideo size={18} aria-hidden /> : <FileAudio size={18} aria-hidden />}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="strong truncate">{value.file.name}</div>
                <div className="faint small">{formatBytes(value.file.size)}</div>
              </div>
              {!disabled && (
                <button type="button" className="btn btn-ghost btn-sm btn-icon" aria-label="Remove file" onClick={() => onChange({ ...value, file: null })}>
                  <X size={16} />
                </button>
              )}
            </div>
          ) : (
            <div
              className={`dropzone${dragging ? " drag" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={value.mode === "video" ? "Choose a video" : "Choose an audio file"}
              onClick={() => !disabled && fileInput.current?.click()}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !disabled && fileInput.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <span className="dz-icon"><UploadCloud size={24} aria-hidden /></span>
              <strong style={{ color: "var(--text)" }}>Drop {value.mode === "video" ? "a video" : "an audio file"} here, or click to browse</strong>
              <span className="small">{MODES.find((m) => m.key === value.mode)!.desc} · up to {MAX_RECORDING_BYTES / 1048576} MB</span>
            </div>
          )}
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT[value.mode]}
            hidden
            data-testid="recording-input"
            onChange={(e) => onChange({ ...value, file: e.target.files?.[0] ?? null })}
          />
          {fileError && <div className="alert alert-danger" role="alert">{fileError}</div>}
          {uploadProgress !== null && (
            <div className="stack-sm">
              <ProgressBar fraction={uploadProgress} label="Upload progress" />
              <span className="muted small">{uploadProgress < 1 ? `Uploading… ${Math.round(uploadProgress * 100)}%` : "Upload complete — verifying the file"}</span>
            </div>
          )}
          <span className="hint">
            {value.mode === "video"
              ? "Only the audio track is used: it is extracted, transcribed with speaker names, and turned into minutes."
              : "The recording uploads straight to secure storage, then is transcribed with speaker names and turned into minutes."}
          </span>
        </div>
      )}

      {value.mode === "later" && (
        <div className="alert alert-info">The meeting will be created without content. Add notes, a transcript, or a recording from its page whenever you're ready.</div>
      )}
    </div>
  );
}

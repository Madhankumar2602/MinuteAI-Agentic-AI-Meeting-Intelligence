import { Clock3, FileAudio, FileText, Mic, UploadCloud, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { ACCEPT_ATTRIBUTE, validateRecording } from "../api/upload";
import { formatBytes } from "../lib/format";
import { MAX_RECORDING_BYTES, MIN_TRANSCRIPT_CHARS, type InputMode, type MeetingInputValue } from "../lib/meetingInput";
import { ProgressBar } from "./ui";

interface Props {
  value: MeetingInputValue;
  onChange: (value: MeetingInputValue) => void;
  allowLater?: boolean;
  disabled?: boolean;
  uploadProgress?: number | null;
}

const MODES: { key: InputMode; label: string; desc: string; icon: typeof FileText }[] = [
  { key: "transcript", label: "Paste transcript", desc: "Text from Zoom, Teams, or notes", icon: FileText },
  { key: "recording", label: "Upload recording", desc: "Audio or video, transcribed for you", icon: Mic },
  { key: "later", label: "Add later", desc: "Create the meeting now", icon: Clock3 },
];

export function MeetingInput({ value, onChange, allowLater = false, disabled = false, uploadProgress = null }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const fileError = value.mode === "recording" && value.file ? validateRecording(value.file, MAX_RECORDING_BYTES) : null;
  const chars = value.transcript.trim().length;
  const lines = value.transcript.split("\n").filter((l) => /^\s*[^:\n]{1,40}:\s*\S/.test(l)).length;

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file && !disabled) onChange({ ...value, file });
  }

  return (
    <div className="stack">
      <div className="mode-cards" role="group" aria-label="Meeting input">
        {MODES.filter((m) => allowLater || m.key !== "later").map(({ key, label, desc, icon: Icon }) => (
          <button key={key} type="button" className="mode-card" aria-pressed={value.mode === key} disabled={disabled} onClick={() => onChange({ ...value, mode: key })}>
            <span className="mode-icon"><Icon size={17} aria-hidden /></span>
            <span className="mode-title">{label}</span>
            <span className="mode-desc">{desc}</span>
          </button>
        ))}
      </div>

      {value.mode === "transcript" && (
        <div className="field">
          <div className="row-between">
            <label className="label" htmlFor="transcript">Transcript</label>
            <span className="faint xs" aria-live="polite">
              {chars > 0 && `${chars.toLocaleString()} characters${lines ? ` · ${lines} speaker lines` : ""}`}
              {chars > 0 && chars < MIN_TRANSCRIPT_CHARS && ` · ${MIN_TRANSCRIPT_CHARS - chars} more needed`}
            </span>
          </div>
          <textarea
            id="transcript"
            className="textarea"
            value={value.transcript}
            disabled={disabled}
            placeholder={"Priya: Let's start with the database migration.\nKarthik: Staging is fully migrated to PostgreSQL 16.\nPriya: Great — Karthik, can you prepare the runbook by Friday?"}
            onChange={(e) => onChange({ ...value, transcript: e.target.value })}
          />
          <span className="hint">
            Tip: one speaker per line as <code>Name: what they said</code>. Named speakers make action-item owners accurate.
          </span>
        </div>
      )}

      {value.mode === "recording" && (
        <div className="stack-sm">
          {value.file ? (
            <div className="file-pill">
              <span className="meeting-icon audio"><FileAudio size={18} aria-hidden /></span>
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
              aria-label="Choose a recording"
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
              <strong style={{ color: "var(--text)" }}>Drop a recording here, or click to browse</strong>
              <span className="small">MP3, WAV, M4A, OGG, FLAC, WebM, MP4, MOV · up to {MAX_RECORDING_BYTES / 1048576} MB</span>
            </div>
          )}
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT_ATTRIBUTE}
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
          <span className="hint">The recording uploads straight to secure storage, then is transcribed and analysed automatically.</span>
        </div>
      )}

      {value.mode === "later" && (
        <div className="alert alert-info">The meeting will be created without content. Add a transcript or recording from its page whenever you're ready.</div>
      )}
    </div>
  );
}
